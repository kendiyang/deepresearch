import asyncio
import os
import json
import logging
import signal
import random
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

# --- 【关键修复 1】环境清理 ---
# 在加载任何网络库之前，强制清除可能干扰 Steel SDK 连接的代理环境变量
# 这样 Steel 客户端连接 1.208.108.242 就会走直连，而不是走 novproxy
for env_key in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy']:
    os.environ.pop(env_key, None)

# 第三方库
import redis.asyncio as redis
from dotenv import load_dotenv
from steel import Steel
from playwright.async_api import async_playwright, Playwright, Browser, Page

# 重新加载 .env (仅用于获取配置字符串，不会自动注入系统代理，因为上面已经清理过)
load_dotenv()

# ==========================================
# 1. 基础配置 (Configuration)
# ==========================================
class AppConfig:
    # 基础设施
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_QUEUE_KEY = os.getenv("REDIS_QUEUE_KEY", "reddit_scraper_queue")
    REDIS_ERROR_QUEUE = os.getenv("REDIS_ERROR_QUEUE", "reddit_scraper_errors")
    
    # Steel 服务端
    STEEL_URL = os.getenv("STEEL_API_URL", "http://1.208.108.242:63870")
    
    # 代理池解析
    _proxy_str = os.getenv("PROXY_POOL", "http://5gye4972-region-US:7fsccucj@us.novproxy.io:443,http://5gye4972-region-US:7fsccucj@hk.novproxy.io:443,http://5gye4972-region-US:7fsccucj@us.novproxy.io:1000,http://5gye4972-region-US:7fsccucj@hk.novproxy.io:1000")

    PROXY_POOL = [p.strip() for p in _proxy_str.split(",") if p.strip()]
    
    # 兼容回退
    if not PROXY_POOL and os.getenv("PROXY_URL"):
        PROXY_POOL = [os.getenv("PROXY_URL")]

    # 策略配置
    CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "3"))
    MAX_RETRIES = 3
    RETRY_DELAY = 5
    TASK_TIMEOUT = 180
    OUTPUT_DIR = os.getenv("OUTPUT_DIR", "data_output")

# 检查
if not AppConfig.PROXY_POOL:
    # 生产环境必须有代理，否则 Reddit 100% 封锁
    logger_temp = logging.getLogger("Init")
    logger_temp.warning("⚠️ 未配置代理池，将在无代理模式下运行 (容易被封)")

os.makedirs(AppConfig.OUTPUT_DIR, exist_ok=True)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(AppConfig.OUTPUT_DIR, "scraper.log"), encoding='utf-8')
    ]
)
logger = logging.getLogger("ScraperSystem")
STOP_EVENT = asyncio.Event()

# ==========================================
# 2. 核心逻辑工具
# ==========================================
def extract_comments_recursively(children_list: Any, parent_id: str, depth: int = 0) -> List[Dict]:
    """递归提取评论数据 (Defensive Parsing)"""
    extracted_data = []
    if not isinstance(children_list, list): return []

    for child in children_list:
        if not isinstance(child, dict): continue
        if child.get('kind') != 't1': continue
        data = child.get('data', {})
        if not isinstance(data, dict): continue

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
            next_children = replies['data'].get('children')
            if isinstance(next_children, list):
                extracted_data.extend(extract_comments_recursively(
                    next_children, data.get("id"), depth + 1
                ))
    return extracted_data

# ==========================================
# 3. Worker 类 (网络隔离 + 代理注入)
# ==========================================
class ScraperWorker:
    def __init__(self, worker_id: int, semaphore: asyncio.Semaphore, redis_client: redis.Redis, playwright: Playwright, steel_client: Steel):
        self.worker_id = worker_id
        self.semaphore = semaphore
        self.redis = redis_client
        self.playwright = playwright
        self.steel_client = steel_client

    def _get_random_proxy(self) -> str:
        if not AppConfig.PROXY_POOL: return None
        return random.choice(AppConfig.PROXY_POOL)

    async def _safe_create_session(self) -> Any:
        """创建会话"""
        # 生成随机指纹
        chrome_version = random.randint(115, 126)
        ua = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version}.0.0.0 Safari/537.36"
        
        # 选定代理
        selected_proxy = self._get_random_proxy()
        proxy_config = {"server": selected_proxy} if selected_proxy else None

        # 将 SDK 调用移入线程池，避免阻塞
        return await asyncio.to_thread(
            self.steel_client.sessions.create,
            use_proxy=proxy_config,
            user_agent=ua,
            concurrency=10,
            dimensions={"width": 1920, "height": 1080},
            timeout=45000 
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
            # 1. 创建 Session (直连 Steel 服务端)
            session = await self._safe_create_session()
            
            # 2. 连接 CDP (通过 Session 的 WebSocket)
            browser = await self.playwright.chromium.connect_over_cdp(session.websocket_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            # 3. 抓取 (此时流量通过 Steel 内部配置的代理走)
            response = await page.goto(url, wait_until="commit", timeout=60000)
            
            if not response: raise Exception("Empty Response")
            
            if response.status == 200:
                return await response.json()
            elif response.status == 429:
                raise Exception("RateLimit 429")
            elif response.status == 403:
                # 代理 IP 被封，抛出异常触发重试（换代理）
                raise Exception("403 Forbidden (Bad Proxy)")
            elif response.status == 404:
                return {"_error_": "404 Not Found"}
            elif response.status >= 500:
                logger.warning(f"[Worker-{self.worker_id}] 🔥 服务端异常 ({response.status})")
                await asyncio.sleep(20)
                raise Exception(f"Server Error {response.status}")
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

        # 启动抖动
        jitter = random.uniform(1.0, 4.0)
        await asyncio.sleep(jitter)

        async with self.semaphore:
            logger.info(f"[Worker-{self.worker_id}] 开始处理: {topic_id} (Jitter: {jitter:.2f}s)")
            
            for attempt in range(1, AppConfig.MAX_RETRIES + 1):
                try:
                    json_data = await asyncio.wait_for(
                        self._execute_crawl(url), 
                        timeout=AppConfig.TASK_TIMEOUT
                    )
                    
                    if json_data and isinstance(json_data, dict) and "_error_" in json_data:
                        logger.error(f"[Worker-{self.worker_id}] ❌ 业务失败: {url} ({json_data['_error_']})")
                        return 

                    if not json_data: raise Exception("No Data")
                    if not isinstance(json_data, list) or len(json_data) < 2:
                        raise Exception("Invalid JSON")

                    post_data_list = json_data[0].get('data', {}).get('children', [])
                    if not post_data_list: raise Exception("No Post Data")
                    post_data = post_data_list[0].get('data', {})
                    
                    comments_data = json_data[1].get('data', {}).get('children', [])
                    all_comments = extract_comments_recursively(comments_data, parent_id=post_data.get('id'))

                    final_data = {
                        "topic_id": topic_id,
                        "source_url": url,
                        "reddit_post_id": post_data.get('id'),
                        "title": post_data.get("title"),
                        "crawled_at": datetime.now().isoformat(),
                        "comments_count": len(all_comments),
                        "comments": all_comments
                    }

                    filename = os.path.join(AppConfig.OUTPUT_DIR, f"{topic_id}.json")
                    await self._safe_write_file(filename, final_data)
                    
                    logger.info(f"[Worker-{self.worker_id}] ✅ 完成 | 尝试: {attempt} | 评论: {len(all_comments)}")
                    return 

                except Exception as e:
                    # 将 Connection Error 和其他错误区分显示
                    err_msg = str(e)
                    is_last = attempt == AppConfig.MAX_RETRIES
                    
                    if is_last:
                        logger.error(f"[Worker-{self.worker_id}] ❌ 最终失败: {err_msg}")
                        await self.redis.lpush(AppConfig.REDIS_ERROR_QUEUE, json.dumps(task_payload))
                    else:
                        logger.warning(f"[Worker-{self.worker_id}] ⚠️ 失败重试: {err_msg}")
                        await asyncio.sleep(AppConfig.RETRY_DELAY * attempt)

# ==========================================
# 4. 主程序 Orchestrator
# ==========================================
async def main():
    logger.info(f"🚀 系统启动 | 代理数量: {len(AppConfig.PROXY_POOL)} | 并发: {AppConfig.CONCURRENCY_LIMIT}")
    logger.info(f"📍 Steel Server: {AppConfig.STEEL_URL}")
    
    redis_client = redis.from_url(AppConfig.REDIS_URL, decode_responses=True)
    semaphore = asyncio.Semaphore(AppConfig.CONCURRENCY_LIMIT)
    
    # 初始化单例资源
    steel_client = Steel(base_url=AppConfig.STEEL_URL)
    
    async with async_playwright() as global_playwright:
        logger.info("✅ 全局资源池已就绪")
        
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
                    pass

            except Exception as e:
                if not STOP_EVENT.is_set():
                    logger.error(f"主循环异常: {e}")
                    await asyncio.sleep(1)

        logger.info("🛑 正在优雅停机...")
        pending = [t for t in tasks if not t.done()]
        if pending:
            logger.info(f"⏳ 等待 {len(pending)} 个任务完成...")
            await asyncio.wait(pending, timeout=30)
        
        await redis_client.aclose()
        logger.info("👋 服务已关闭")

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