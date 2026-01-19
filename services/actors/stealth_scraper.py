import asyncio
import logging
import json
import random
import os
import sys
import signal
import uuid
import mimetypes
import hashlib
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Literal
from urllib.parse import urlparse, urlencode, urlunparse

# --- 第三方库 ---
import aiofiles
import aiosqlite
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, RequestsError
from aiobotocore.session import get_session

# Pydantic V2
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# 选装 Trafilatura
try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

# ================= 1. 深度配置层 =================
class AppConfig(BaseSettings):
    APP_NAME: str = "HybridScraper_Pro"
    DATA_DIR: str = "./data"
    DB_NAME: str = "task_queue.db"
    
    STORAGE_TYPE: Literal["local", "s3"] = Field(default="local")
    
    S3_ENDPOINT_URL: Optional[str] = "http://localhost:9000"
    S3_ACCESS_KEY: Optional[str] = "minioadmin"
    S3_SECRET_KEY: Optional[str] = "minioadmin"
    S3_BUCKET_NAME: str = "crawler-data"
    S3_REGION_NAME: str = "us-east-1"
    S3_BATCH_SIZE: int = 50
    
    # 策略配置
    MAX_CONCURRENCY: int = 3       # Reddit 建议降低并发，避免封 IP 段
    MAX_RETRIES: int = 5           # 增加重试次数
    REQUEST_TIMEOUT: int = 30
    
    # 代理池 (建议使用轮换代理)
    PROXY_LIST: List[str] = [
        "http://5gye4972-region-US-sid-VP4v8Nsj-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-cfDbzT2U-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-Rr1DKxDS-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-R7NdWqFN-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-Vd1vYtdp-t-1:7fsccucj@us.novproxy.io:443"
    ] 

    model_config = SettingsConfigDict(
        env_prefix="CRAWLER_",
        env_file=".env",
        extra="ignore"
    )

CONFIG = AppConfig()

# 日志配置
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "lvl": record.levelname,
            "msg": record.getMessage(),
            "mod": record.module,
        }
        return json.dumps(log_obj, ensure_ascii=False)

logger = logging.getLogger(CONFIG.APP_NAME)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)

# ================= 2. 平台处理器 =================

class RedditHandler:
    @staticmethod
    def optimize_url(url: str) -> str:
        """
        Reddit 核心破解逻辑：
        1. 强制使用 old.reddit.com (鉴权最宽松)
        2. 确保 .json 后缀
        3. 清理无用参数
        """
        parsed = urlparse(url)
        
        # 1. 强制替换域名为 old.reddit.com
        new_netloc = "old.reddit.com"
        
        # 2. 处理路径，确保以 .json 结尾
        path = parsed.path.rstrip("/")
        if not path.endswith(".json"):
            path += ".json"
            
        return urlunparse((
            parsed.scheme,
            new_netloc,
            path,
            parsed.params,
            parsed.query,  # 保留 sort=new 等参数
            parsed.fragment
        ))

    @staticmethod
    def parse_response(data: Dict, original_url: str) -> Dict:
        """解析 Reddit JSON"""
        try:
            content_type = "reddit_listing"
            extracted_data = {}

            # 详情页结构: [PostData, CommentsData]
            if isinstance(data, list) and len(data) > 0:
                content_type = "reddit_post_detail"
                post_container = data[0].get('data', {}).get('children', [{}])[0].get('data', {})
                
                # 评论处理
                comments_data = []
                if len(data) > 1:
                    raw_comments = data[1].get('data', {}).get('children', [])
                    for c in raw_comments[:10]: # 提取前10条
                        if c.get('kind') == 't1': # t1 是评论
                            comments_data.append({
                                "author": c['data'].get('author'),
                                "body": c['data'].get('body'),
                                "ups": c['data'].get('ups')
                            })

                extracted_data = {
                    "id": post_container.get("id"),
                    "title": post_container.get("title"),
                    "selftext": post_container.get("selftext"),
                    "author": post_container.get("author"),
                    "ups": post_container.get("ups"),
                    "upvote_ratio": post_container.get("upvote_ratio"),
                    "created_utc": post_container.get("created_utc"),
                    "comments": comments_data
                }
            
            # 列表页结构
            elif isinstance(data, dict):
                children = data.get("data", {}).get("children", [])
                extracted_data = {
                    "type": "listing",
                    "posts": [{
                        "id": c['data'].get('id'),
                        "title": c['data'].get('title'),
                        "url": c['data'].get('url'),
                        "permalink": c['data'].get('permalink')
                    } for c in children if c.get('kind') == 't3'] # t3 是帖子
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
            return {"url": original_url, "error": f"Parse Error: {str(e)}", "raw_partial": str(data)[:100]}

class TikTokHandler:
    @staticmethod
    def extract_data(html: str, url: str) -> Optional[Dict]:
        # 针对 TikTok 的通用提取，保持不变
        patterns = [
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">([^<]+)</script>',
            r'<script id="SIGI_STATE" type="application/json">([^<]+)</script>'
        ]
        
        for p in patterns:
            match = re.search(p, html)
            if match:
                try:
                    raw_data = json.loads(match.group(1))
                    return {
                        "url": url,
                        "platform": "tiktok",
                        "type": "video_meta",
                        "raw_data": raw_data,
                        "timestamp": datetime.now().isoformat()
                    }
                except:
                    continue
        return None

# ================= 3. 存储层 (保持简洁) =================
class StorageBackend(ABC):
    @abstractmethod
    async def initialize(self): pass
    @abstractmethod
    async def save_data(self, record: Dict): pass
    @abstractmethod
    async def close(self): pass

class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.data_file = os.path.join(base_dir, "scraped_data.jsonl")
        self._file_handle = None

    async def initialize(self):
        os.makedirs(self.base_dir, exist_ok=True)
        self._file_handle = await aiofiles.open(self.data_file, mode='a', encoding='utf-8', buffering=1)

    async def save_data(self, record: Dict):
        if self._file_handle:
            await self._file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def close(self):
        if self._file_handle: await self._file_handle.close()

# S3 实现略去以节省篇幅，逻辑同上文...

# ================= 4. 任务管理器 =================
class TaskManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = None
        self._lock = asyncio.Lock()

    async def init_db(self):
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                url TEXT PRIMARY KEY,
                status TEXT DEFAULT 'PENDING',
                retry_count INTEGER DEFAULT 0,
                last_error TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.commit()

    async def add_tasks(self, urls: List[str]):
        if not urls: return
        async with self._lock:
            await self._db.executemany(
                "INSERT OR IGNORE INTO tasks (url) VALUES (?)", [(u,) for u in urls]
            )
            await self._db.commit()

    async def acquire_task(self) -> Optional[Tuple[str, int]]:
        # 优先重试次数少的老任务
        q = """
            UPDATE tasks
            SET status = 'PROCESSING', updated_at = CURRENT_TIMESTAMP
            WHERE url = (
                SELECT url FROM tasks 
                WHERE status = 'PENDING' 
                OR (status = 'FAILED' AND retry_count < ?)
                ORDER BY retry_count ASC, updated_at ASC
                LIMIT 1
            )
            RETURNING url, retry_count
        """
        async with self._lock:
            try:
                async with self._db.execute(q, (CONFIG.MAX_RETRIES,)) as cursor:
                    row = await cursor.fetchone()
                    if row: await self._db.commit()
                    return row
            except Exception as e:
                logger.error(f"DB Error: {e}")
        return None

    async def update_task(self, url: str, status: str, error_msg: str = None):
        async with self._lock:
            if status == 'FAILED':
                await self._db.execute(
                    "UPDATE tasks SET status=?, retry_count=retry_count+1, last_error=? WHERE url=?", 
                    (status, error_msg, url)
                )
            else:
                await self._db.execute("UPDATE tasks SET status=?, last_error=NULL WHERE url=?", (status, url))
            await self._db.commit()
    
    async def close(self):
        if self._db: await self._db.close()

# ================= 5. 网络引擎 (核心修复区) =================
class NetworkEngine:
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        # ⚠️ 只使用 Chrome 指纹，因为 Reddit 对 Chrome 支持最好
        self.impersonates = ["chrome110", "chrome120", "chrome124"]

    def _get_random_proxy(self) -> Optional[str]:
        if not CONFIG.PROXY_LIST:
            return None
        return random.choice(CONFIG.PROXY_LIST)

    async def fetch_and_process(self, url: str) -> Literal["SUCCESS", "RETRY", "FATAL", "SKIP"]:
        """
        核心抓取逻辑
        """
        proxy = self._get_random_proxy()
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        
        target_url = url
        is_reddit = "reddit.com" in url
        is_tiktok = "tiktok.com" in url

        # 1. 针对 Reddit 的 URL 预处理 (强制 old.reddit.com)
        if is_reddit:
            target_url = RedditHandler.optimize_url(url)
        
        # 2. 构造 Headers
        # 注意：使用 curl_cffi 时，不要随便覆盖 User-Agent，除非你确信不会破坏 TLS 指纹
        # 这里的策略是：让 impersonate 处理 User-Agent，我们只补充语义 Header
        headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        }

        # Reddit 专用 Header (模拟 API 调用意图)
        if is_reddit:
            headers["Accept"] = "application/json, text/javascript, */*; q=0.01"
            headers["X-Requested-With"] = "XMLHttpRequest"

        try:
            # 随机延迟，避免并发过高触发风控
            await asyncio.sleep(random.uniform(2.0, 5.0))
            
            chosen_impersonate = random.choice(self.impersonates)
            
            logger.debug(f"🔍 Requesting: {target_url} | Impersonate: {chosen_impersonate}")
            
            async with AsyncSession(
                impersonate=chosen_impersonate,
                proxies=proxies,
                headers=headers,
                timeout=CONFIG.REQUEST_TIMEOUT,
                verify=False # 忽略 SSL 验证有时能绕过某些弱防火墙
            ) as s:
                response = await s.get(target_url)
                
                # --- 状态码处理 ---
                if response.status_code == 404:
                    logger.warning(f"⛔️ 404 Not Found: {target_url}")
                    return "FATAL" # 无需重试

                if response.status_code in [403, 429]:
                    logger.warning(f"🛡️ 403/429 Blocked ({target_url}) - Suggestion: Rotate Proxy")
                    return "RETRY"

                if response.status_code != 200:
                    logger.warning(f"⚠️ HTTP {response.status_code}")
                    return "RETRY"

                # --- Reddit 数据处理 ---
                if is_reddit:
                    try:
                        # 检查内容是否真的是 JSON
                        if "application/json" not in response.headers.get("content-type", ""):
                            # 即使是 200，如果返回 HTML 也是被盾了
                            # 很多时候 Reddit 200 返回一个 HTML 让你“Verify your email”
                            logger.error(f"❌ Reddit returned HTML instead of JSON (Soft Block): {target_url}")
                            return "RETRY"

                        json_data = response.json()
                        parsed = RedditHandler.parse_response(json_data, url)
                        await self.storage.save_data(parsed)
                        logger.info(f"✅ Reddit Success: {url}")
                        return "SUCCESS"
                    except json.JSONDecodeError:
                        logger.error("❌ JSON Decode Error")
                        return "RETRY"

                # --- TikTok 数据处理 ---
                if is_tiktok:
                    tiktok_data = TikTokHandler.extract_data(response.text, url)
                    if tiktok_data:
                        await self.storage.save_data(tiktok_data)
                        logger.info(f"✅ TikTok Success: {url}")
                        return "SUCCESS"
                    else:
                        logger.warning(f"⚠️ TikTok Meta Not Found: {url}")
                        # 失败后可以尝试保存 HTML 以后分析
                        return "RETRY"

                # --- 默认 HTML 处理 ---
                # ... (通用处理逻辑)
                
                return "SUCCESS"

        except RequestsError as e:
            logger.error(f"❌ Network Error: {e}")
            return "RETRY"
        except Exception as e:
            logger.error(f"❌ Unexpected Error: {e}")
            return "RETRY"

# ================= 6. 主程序 =================
class CrawlerEngine:
    def __init__(self):
        if CONFIG.STORAGE_TYPE == "local":
            self.storage = LocalStorage(CONFIG.DATA_DIR)
        else:
            # S3 初始化逻辑
            pass
            
        self.db = TaskManager(os.path.join(CONFIG.DATA_DIR, CONFIG.DB_NAME))
        self.network = NetworkEngine(self.storage)
        self.shutdown_event = asyncio.Event()

    async def worker(self, worker_id: int):
        logger.info(f"👷 Worker-{worker_id} started")
        while not self.shutdown_event.is_set():
            try:
                task = await self.db.acquire_task()
                if not task:
                    await asyncio.sleep(2)
                    continue
                
                url, retry_cnt = task
                
                # 指数退避策略：失败次数越多，等待越久
                if retry_cnt > 0:
                    delay = min(30, 2 ** retry_cnt)
                    logger.info(f"⏳ Backoff {delay}s for retry {retry_cnt}: {url}")
                    await asyncio.sleep(delay)

                logger.info(f"▶️ Processing: {url}")
                status = await self.network.fetch_and_process(url)
                
                if status == "SUCCESS":
                    await self.db.update_task(url, "COMPLETED")
                elif status == "FATAL":
                    await self.db.update_task(url, "FAILED_FATAL", error_msg="404 or Logic Error")
                elif status == "RETRY":
                    await self.db.update_task(url, "FAILED", error_msg="Network/Auth Error")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker Exception: {e}")
                await asyncio.sleep(1)

    async def run(self, seeds: List[str]):
        await self.db.init_db()
        await self.db.add_tasks(seeds)
        await self.storage.initialize()
        
        workers = [asyncio.create_task(self.worker(i)) for i in range(CONFIG.MAX_CONCURRENCY)]
        
        # 优雅退出逻辑
        def signal_handler():
            self.shutdown_event.set()
            logger.info("🛑 Stopping...")

        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGINT, signal_handler)
        
        try:
            # 简单的主循环监控
            while not self.shutdown_event.is_set():
                if all(w.done() for w in workers):
                    break
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            signal_handler()
        finally:
            self.shutdown_event.set()
            await asyncio.gather(*workers, return_exceptions=True)
            await self.db.close()
            await self.storage.close()

if __name__ == "__main__":
    # 混合测试种子
    targets = [
       "https://www.reddit.com/r/DIYfragrance/comments/1oj09hp/recommendations_for_incense_fragrance",
    "https://www.reddit.com/r/fragrance/comments/1jdl45d/does_anybody_else_find_frankincense_to_be_a_sexy",
    "https://www.reddit.com/r/DIYfragrance/comments/1klxda6/top_5_raw_ingredients",
    "https://www.reddit.com/r/DIYfragrance/comments/1nuewt8/best_natural_essential_oil_combos_for_masculine",
    "https://www.reddit.com/r/Incense/comments/1k9xbr4/cultural_historical_and_ceremonial_uses_of",
    "https://www.reddit.com/r/AsianBeauty/comments/1ojacpn/different_ingredients_in_us_vs_korea_skin_1004",
    "https://www.reddit.com/r/DIYfragrance/comments/1lkyv5s/incense_accord",
    "https://www.reddit.com/r/SkincareAddiction/comments/1q02upp/antiaging_is_anyone_familiar_with_this_brand",
    "https://www.reddit.com/r/SkincareAddiction/comments/1oy5he8/antiaging_best_look_younger_single_step_product",
    "https://www.reddit.com/r/AsianBeauty/comments/1poki78/antiaging_high_end_product_recommendations",
    "https://www.reddit.com/r/AskMenOver30/comments/1oa0qu8/has_anyone_actually_noticed_results_from",
    "https://www.reddit.com/r/SkincareAddictionLux/comments/1otf69z/favorite_antiaging_face_oil_with_antioxidants",
    "https://www.reddit.com/r/Sephora/comments/1l5fjtb/what_antiaging_products_visably_made_such_a", 
        # TikTok 种子
        "https://www.tiktok.com/@tiktok/video/7306352936746208544",
    ]
    
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    engine = CrawlerEngine()
    asyncio.run(engine.run(targets))
