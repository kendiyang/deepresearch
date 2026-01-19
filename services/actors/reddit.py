import asyncio
import logging
import json
import os
import sys
import signal
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Literal
from urllib.parse import urlparse, urlencode, urlunparse

# --- 第三方库 ---
import aiofiles
import asyncpg
from bs4 import BeautifulSoup

# Playwright
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page, Error as PlaywrightError

# Pydantic
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# ================= 1. 深度配置层 =================
class AppConfig(BaseSettings):
    APP_NAME: str = "Steel_Scraper_Enterprise"
    DATA_DIR: str = "./data"
    
    # DB
    PG_USER: str = "tenmuses"
    PG_PASSWORD: str = "tenmuses_dev"
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_DB: str = "tenmuses"
    
    # Steel.dev
    STEEL_API_KEY: str = "ste-mZrTnUvD3HpKKFC874iUH1TykJi1VdgFaK42nsNS1ZVlQ3LB3gTAOM1x1MPHQHqiaDn6NY35HNjBerAARL7vCK7RE1JqgmHPxKp"
    STEEL_WS_ENDPOINT: str = "wss://connect.steel.dev"
    
    # 策略配置
    MAX_CONCURRENCY: int = 5
    MAX_RETRIES: int = 3
    # 增加超时冗余，适应云端浏览器延迟
    REQUEST_TIMEOUT: int = 60000 
    # Browser 连接最大存活时间，强制重连以防止内存泄漏
    BROWSER_TTL: int = 3600 

    model_config = SettingsConfigDict(
        env_prefix="CRAWLER_",
        env_file=".env",
        extra="ignore"
    )

try:
    CONFIG = AppConfig()
except Exception as e:
    print(f"❌ Config Error: {e}")
    sys.exit(1)

# ================= 2. 日志 (保持结构化) =================
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lvl": record.levelname,
            "msg": record.getMessage(),
            "mod": record.module,
        }
        if hasattr(record, 'url'): log_obj['url'] = record.url
        return json.dumps(log_obj, ensure_ascii=False)

logger = logging.getLogger(CONFIG.APP_NAME)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)

# ================= 3. 平台业务逻辑 (无状态纯函数) =================
class RedditHandler:
    @staticmethod
    def optimize_url(url: str) -> str:
        parsed = urlparse(url)
        # old.reddit + .json 是最稳定低成本的方案
        new_netloc = "old.reddit.com" 
        path = parsed.path.rstrip("/")
        if not path.endswith(".json"):
            path += ".json"
        return urlunparse((parsed.scheme, new_netloc, path, parsed.params, parsed.query, parsed.fragment))

    @staticmethod
    def parse_response(data: Dict, original_url: str) -> Dict:
        """解析 Reddit JSON 响应"""
        try:
            content_type = "reddit_listing"
            extracted_data = {}

            # 情况 A: 详情页 (返回列表: [Post, Comments])
            if isinstance(data, list) and len(data) > 0:
                content_type = "reddit_post_detail"
                post_data = data[0].get('data', {}).get('children', [{}])[0].get('data', {})
                comments_data = data[1].get('data', {}).get('children', []) if len(data) > 1 else []
                
                extracted_data = {
                    "title": post_data.get("title"),
                    "selftext": post_data.get("selftext"),
                    "author": post_data.get("author"),
                    "ups": post_data.get("ups"),
                    "upvote_ratio": post_data.get("upvote_ratio"),
                    "comment_count": post_data.get("num_comments"),
                    "created_utc": post_data.get("created_utc"),
                    "top_comments": [c['data'].get('body') for c in comments_data[:3] if 'body' in c.get('data', {})]
                }
            
            # 情况 B: 列表页 (Subreddit Listing)
            elif isinstance(data, dict):
                children = data.get("data", {}).get("children", [])
                extracted_data = {
                    "post_count": len(children),
                    "posts": [
                        {
                            "title": c['data'].get('title'),
                            "url": c['data'].get('url')
                        } 
                        for c in children[:5]
                    ]
                }

            return {
                "url": original_url,
                "platform": "reddit",
                "type": content_type,
                "parsed": extracted_data,
                "raw_json": data, 
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"url": original_url, "error": str(e), "raw_partial": str(data)[:200]}

class TikTokHandler:
    @staticmethod
    def extract_from_state(sigi_state: Dict, url: str) -> Optional[Dict]:
        try:
            item_module = sigi_state.get('ItemModule', {})
            video_data = next(iter(item_module.values()), {}) if item_module else {}
            return {
                "url": url,
                "platform": "tiktok",
                "video_id": video_data.get('id'),
                "desc": video_data.get('desc'),
                "stats": video_data.get('stats'),
                "timestamp": datetime.now().isoformat()
            }
        except Exception:
            return None

# ================= 4. 存储与任务队列 (PostgreSQL) =================
class TaskManager:
    def __init__(self, config: AppConfig):
        self.config = config
        self.pool: Optional[asyncpg.Pool] = None

    async def init_db(self):
        # 优化连接池大小：并发数 + 预留数
        self.pool = await asyncpg.create_pool(
            user=self.config.PG_USER,
            password=self.config.PG_PASSWORD,
            host=self.config.PG_HOST,
            port=self.config.PG_PORT,
            database=self.config.PG_DB,
            min_size=self.config.MAX_CONCURRENCY,
            max_size=self.config.MAX_CONCURRENCY + 5
        )
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    url TEXT PRIMARY KEY,
                    status TEXT DEFAULT 'PENDING',
                    retry_count INTEGER DEFAULT 0,
                    last_error TEXT,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
            """)

    async def add_tasks(self, urls: List[str]):
        if not urls: return
        query = "INSERT INTO tasks (url) VALUES ($1) ON CONFLICT (url) DO NOTHING"
        async with self.pool.acquire() as conn:
            await conn.executemany(query, [(u,) for u in urls])

    async def acquire_task(self) -> Optional[Tuple[str, int]]:
        # SKIP LOCKED 是实现高并发队列的关键
        query = """
            WITH next_task AS (
                SELECT url
                FROM tasks 
                WHERE status = 'PENDING' 
                   OR (status = 'FAILED' AND retry_count < $1)
                ORDER BY retry_count ASC, updated_at ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE tasks
            SET status = 'PROCESSING', updated_at = CURRENT_TIMESTAMP
            FROM next_task
            WHERE tasks.url = next_task.url
            RETURNING tasks.url, tasks.retry_count;
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, self.config.MAX_RETRIES)
                return (row['url'], row['retry_count']) if row else None
        except Exception as e:
            logger.error(f"DB Error: {e}")
            return None

    async def update_task(self, url: str, status: str, error_msg: str = None):
        async with self.pool.acquire() as conn:
            if status.startswith('FAILED'):
                await conn.execute(
                    "UPDATE tasks SET status=$1, retry_count=retry_count+1, last_error=$2 WHERE url=$3", 
                    status, error_msg, url
                )
            else:
                await conn.execute(
                    "UPDATE tasks SET status=$1, last_error=NULL WHERE url=$2", 
                    status, url
                )

    async def close(self):
        if self.pool: await self.pool.close()

class LocalStorage:
    def __init__(self, base_dir: str):
        self.path = os.path.join(base_dir, "data.jsonl")
        self._f = None
    
    async def initialize(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._f = await aiofiles.open(self.path, 'a', encoding='utf-8')

    async def save(self, data: Dict):
        if self._f: await self._f.write(json.dumps(data, ensure_ascii=False) + "\n")

    async def close(self):
        if self._f: await self._f.close()

# ================= 5. 网络引擎 (Steel.dev 增强版) =================
class SteelNetworkManager:
    """
    负责管理 Playwright 实例和 Steel.dev 的 WebSocket 连接。
    具备自动重连和会话生命周期管理功能。
    """
    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self._connect_lock = asyncio.Lock()
        self._last_connect_time = 0

    async def get_browser(self) -> Browser:
        """获取可用的 Browser 实例，如果断开或过期则重连"""
        current_time = datetime.now().timestamp()
        
        # 检查连接是否存活且未过期
        is_connected = self.browser and self.playwright and self.browser.is_connected()
        is_expired = (current_time - self._last_connect_time) > CONFIG.BROWSER_TTL

        if is_connected and not is_expired:
            return self.browser

        async with self._connect_lock:
            # 双重检查
            if self.browser and self.browser.is_connected() and \
               (datetime.now().timestamp() - self._last_connect_time) <= CONFIG.BROWSER_TTL:
                return self.browser

            logger.info("🔌 Establishing new Steel.dev session...")
            
            # 关闭旧资源
            if self.browser: 
                try: await self.browser.close()
                except: pass
            if self.playwright:
                try: await self.playwright.stop()
                except: pass

            self.playwright = await async_playwright().start()
            
            # Steel 参数配置
            params = {
                "apiKey": CONFIG.STEEL_API_KEY,
                "stealth": "true",       # 开启隐身
                "solveCaptcha": "true",  # 开启验证码自动解决
                "blockAds": "true",      # 屏蔽广告 (Steel 服务端功能)
            }
            ws_url = f"{CONFIG.STEEL_WS_ENDPOINT}?{urlencode(params)}"
            
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(ws_url)
                self._last_connect_time = current_time
                logger.info("✅ Connected to Steel.dev")
                return self.browser
            except Exception as e:
                logger.error(f"❌ Failed to connect to Steel: {e}")
                raise

    async def close(self):
        if self.browser: await self.browser.close()
        if self.playwright: await self.playwright.stop()

class PageProcessor:
    """处理单个页面的生命周期"""
    
    @staticmethod
    async def process(browser: Browser, url: str, storage: LocalStorage) -> Literal["SUCCESS", "RETRY", "FATAL", "SKIP"]:
        context = None
        page = None
        try:
            # 创建独立的 Context，确保 Cookie/Storage 隔离
            # 这里的 viewport 设置有助于规避某些指纹检测
            context = await browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
            )
            
            # 策略：屏蔽资源以节省 Steel 带宽计费和加速
            # Reddit JSON 只需要文本，TikTok 需要脚本
            if "reddit.com" in url:
                await context.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2}", lambda route: route.abort())

            page = await context.new_page()
            
            # --- Reddit 逻辑 ---
            if "reddit.com" in url:
                target_url = RedditHandler.optimize_url(url)
                logger.info(f"Downloading: {target_url}", extra={'url': url})
                
                # 直接获取 JSON，无需渲染完整 DOM
                resp = await page.goto(target_url, timeout=CONFIG.REQUEST_TIMEOUT, wait_until="commit")
                
                if resp.status == 429: return "RETRY"
                if resp.status != 200: return "FATAL" # 404 等

                try:
                    data = await resp.json() # 尝试直接解析
                    parsed = RedditHandler.parse_response(data, url)
                    await storage.save(parsed)
                    return "SUCCESS"
                except:
                    # 如果返回的不是 JSON（可能是 Cloudflare 拦截页），则需要重试或截图分析
                    # await page.screenshot(path="debug_reddit.png")
                    return "RETRY"

            # --- TikTok 逻辑 ---
            elif "tiktok.com" in url:
                logger.info(f"Rendering: {url}", extra={'url': url})
                await page.goto(url, timeout=CONFIG.REQUEST_TIMEOUT, wait_until="domcontentloaded")
                
                # 等待核心数据对象
                try:
                    # 轮询检查 window.SIGI_STATE，比 sleep 更高效
                    handle = await page.wait_for_function("() => window.SIGI_STATE", timeout=15000)
                    sigi_state = await handle.json_value()
                    
                    parsed = TikTokHandler.extract_from_state(sigi_state, url)
                    if parsed:
                        await storage.save(parsed)
                        return "SUCCESS"
                    return "FATAL" # 有状态但无数据
                except PlaywrightError:
                    logger.warning(f"TikTok hydration timeout: {url}")
                    return "RETRY"

            return "SKIP"

        except PlaywrightError as pe:
            logger.error(f"Playwright Error: {pe}")
            # 如果是连接断开，抛出异常让上层触发重连
            if "Target closed" in str(pe) or "Session closed" in str(pe):
                raise ConnectionError("Steel session lost")
            return "RETRY"
        except Exception as e:
            logger.error(f"Logic Error: {e}")
            return "RETRY"
        finally:
            # 无论成功失败，务必关闭 Context 以释放 Steel 资源
            if context: await context.close()

# ================= 6. 主引擎 =================
class CrawlerEngine:
    def __init__(self):
        self.db = TaskManager(CONFIG)
        self.storage = LocalStorage(CONFIG.DATA_DIR)
        self.net_manager = SteelNetworkManager()
        self.stop_event = asyncio.Event()

    async def worker(self, worker_id: int):
        logger.info(f"👷 Worker-{worker_id} started")
        
        while not self.stop_event.is_set():
            try:
                # 1. 获取任务
                task = await self.db.acquire_task()
                if not task:
                    await asyncio.sleep(1) # 避免空转
                    continue
                
                url, retry_cnt = task
                
                # 2. 指数退避
                if retry_cnt > 0:
                    await asyncio.sleep(min(10, 2 ** retry_cnt))

                # 3. 执行处理 (带自动重连逻辑)
                try:
                    browser = await self.net_manager.get_browser()
                    result = await PageProcessor.process(browser, url, self.storage)
                except ConnectionError:
                    # 捕获特定的连接错误，强制重置并重试
                    logger.warning(f"Worker-{worker_id} detected disconnection, requesting reconnect...")
                    await asyncio.sleep(random.uniform(1, 3)) # 随机抖动避免惊群
                    # 标记为 RETRY，让下一个循环处理
                    result = "RETRY" 

                # 4. 更新状态
                if result == "SUCCESS":
                    await self.db.update_task(url, "COMPLETED")
                    logger.info(f"✅ Finished: {url}")
                elif result == "FATAL":
                    await self.db.update_task(url, "FAILED_FATAL")
                elif result == "RETRY":
                    await self.db.update_task(url, "FAILED", "Network/Logic Error")
                elif result == "SKIP":
                    await self.db.update_task(url, "SKIPPED")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker-{worker_id} Critical: {e}")
                await asyncio.sleep(5)

    async def run(self, seeds: List[str]):
        await self.db.init_db()
        await self.db.add_tasks(seeds)
        await self.storage.initialize()
        
        # 预热连接
        try:
            await self.net_manager.get_browser()
        except Exception:
            logger.critical("Could not establish initial connection to Steel.dev")
            return

        workers = [asyncio.create_task(self.worker(i)) for i in range(CONFIG.MAX_CONCURRENCY)]

        # 优雅退出钩子
        def stop_signal():
            logger.info("🛑 Stop signal received")
            self.stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_signal)

        # 等待停止
        await self.stop_event.wait()
        
        # 清理
        logger.info("🧹 Shutting down workers...")
        await asyncio.gather(*workers, return_exceptions=True)
        await self.net_manager.close()
        await self.db.close()
        await self.storage.close()
        logger.info("👋 Bye.")

if __name__ == "__main__":
    import random # 用于 Worker 抖动
    # Windows 兼容性
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    seed_urls = [
        "https://www.reddit.com/r/DIYfragrance/comments/1oj09hp/recommendations_for_incense_fragrance",
        "https://www.reddit.com/r/fragrance/comments/1jdl45d/does_anybody_else_find_frankincense_to_be_a_sexy"
    ]
    
    asyncio.run(CrawlerEngine().run(seed_urls))