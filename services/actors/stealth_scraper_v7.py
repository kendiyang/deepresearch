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
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple, Literal, Set
from urllib.parse import urlparse, urlencode, urlunparse
from contextlib import asynccontextmanager

# --- 第三方库 ---
import aiofiles
import asyncpg
import trafilatura
# 显式导入 extract_metadata，防止版本差异导致的引用错误
from trafilatura import extract, extract_metadata 
from curl_cffi.requests import AsyncSession, RequestsError
from aiobotocore.session import get_session

# Pydantic V2
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# ================= 1. 配置层 (Configuration) =================
class AppConfig(BaseSettings):
    APP_NAME: str = "EnterpriseScraper"
    DATA_DIR: str = "./data"
    
    # 存储设置
    STORAGE_TYPE: Literal["local", "s3"] = Field(default="local")
    
    # PostgreSQL 配置
    PG_DSN: str = "postgresql://tenmuses:tenmuses_dev@localhost:5432/tenmuses"
    PG_MIN_SIZE: int = 5
    PG_MAX_SIZE: int = 20

    # S3 / MinIO (可选)
    S3_ENDPOINT_URL: Optional[str] = "http://localhost:9000"
    S3_ACCESS_KEY: Optional[str] = "minioadmin"
    S3_SECRET_KEY: Optional[str] = "minioadmin"
    S3_BUCKET_NAME: str = "crawler-data"
    S3_REGION_NAME: str = "us-east-1"
    S3_BATCH_SIZE: int = 50
    
    # 爬虫行为
    MAX_CONCURRENCY: int = 5 # 建议先调低并发调试
    MAX_RETRIES: int = 3
    REQUEST_TIMEOUT: int = 30
    
    # 代理池配置
    PROXY_LIST: List[str] = [
        "http://127.0.0.1:1080"
    ]
    PROXY_COOLDOWN_SECONDS: int = 300
    PROXY_MAX_FAILURES: int = 5

    model_config = SettingsConfigDict(env_prefix="CRAWLER_", env_file=".env", extra="ignore")

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

# ================= 2. 智能代理池管理 =================
class ProxyManager:
    def __init__(self, proxies: List[str]):
        self.proxies = proxies
        self._stats: Dict[str, Dict] = {p: {"failures": 0, "cooldown_until": 0} for p in proxies}
        self._lock = asyncio.Lock()

    async def get_proxy(self) -> Optional[str]:
        async with self._lock:
            now = time.time()
            candidates = []
            
            for p in self.proxies:
                stats = self._stats[p]
                # 检查冷却时间
                if stats["cooldown_until"] > now:
                    continue 
                candidates.append(p)
            
            if not candidates:
                logger.warning("⚠️ All proxies are in cooldown or unavailable!")
                return None # 明确返回 None

            return random.choice(candidates)

    async def report_status(self, proxy: str, status_code: int):
        if not proxy: return
        
        async with self._lock:
            stats = self._stats.get(proxy)
            if not stats: return

            # 严重错误：403, 429, 407 (Proxy Auth Required)
            if status_code in [429, 403, 407]:
                logger.warning(f"🚫 Proxy {proxy} blocked ({status_code}). Cooling down for {CONFIG.PROXY_COOLDOWN_SECONDS}s.")
                stats["cooldown_until"] = time.time() + CONFIG.PROXY_COOLDOWN_SECONDS
                stats["failures"] += 1
            
            elif status_code == 200:
                stats["failures"] = 0
            
            elif status_code == 0 or status_code >= 500:
                stats["failures"] += 1
                if stats["failures"] >= CONFIG.PROXY_MAX_FAILURES:
                     logger.warning(f"⚠️ Proxy {proxy} unstable. Cooling down.")
                     stats["cooldown_until"] = time.time() + 60
                     stats["failures"] = 0

# ================= 3. 平台解析器 =================

class RedditHandler:
    @staticmethod
    def convert_to_api_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.path.endswith(".json"): return url
        clean_path = parsed.path.rstrip("/")
        new_path = f"{clean_path}.json"
        return urlunparse((parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment))

    @staticmethod
    def parse_response(data: Dict, original_url: str) -> Tuple[Dict, List[str]]:
        new_tasks = []
        parsed_data = {}
        content_type = "reddit_unknown"

        try:
            if isinstance(data, list) and len(data) > 0:
                content_type = "reddit_post_detail"
                post_data = data[0].get('data', {}).get('children', [{}])[0].get('data', {})
                comments_data = data[1].get('data', {}).get('children', []) if len(data) > 1 else []
                
                parsed_data = {
                    "id": post_data.get("id"),
                    "title": post_data.get("title"),
                    "selftext": post_data.get("selftext"),
                    "author": post_data.get("author"),
                    "ups": post_data.get("ups"),
                    "created_utc": post_data.get("created_utc"),
                    "comments": [c['data'].get('body') for c in comments_data[:5] if 'body' in c.get('data', {})]
                }
            
            elif isinstance(data, dict):
                content_type = "reddit_listing"
                d = data.get("data", {})
                children = d.get("children", [])
                
                posts = []
                for c in children:
                    p_data = c.get('data', {})
                    posts.append({
                        "id": p_data.get("id"),
                        "title": p_data.get("title"),
                        "url": p_data.get("url"),
                        "permalink": f"https://www.reddit.com{p_data.get('permalink')}"
                    })

                parsed_data = {"post_count": len(posts), "posts": posts}

                after_cursor = d.get("after")
                if after_cursor:
                    base_parsed = urlparse(original_url)
                    query_params = {k: v for k, v in [p.split('=') for p in base_parsed.query.split('&') if '=' in p]}
                    query_params['after'] = after_cursor
                    next_page_query = urlencode(query_params)
                    next_page_url = urlunparse((
                        base_parsed.scheme, base_parsed.netloc, base_parsed.path,
                        base_parsed.params, next_page_query, base_parsed.fragment
                    ))
                    new_tasks.append(next_page_url)
                    logger.info(f"📄 Found next page: {after_cursor}")

            return {
                "url": original_url,
                "platform": "reddit",
                "type": content_type,
                "data": parsed_data,
                "ts": datetime.now().isoformat()
            }, new_tasks

        except Exception as e:
            logger.error(f"Reddit Parse Error: {e}")
            return {"url": original_url, "error": str(e)}, []

# ================= 4. 存储层 =================
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
            await self._file_handle.flush()

    async def close(self):
        if self._file_handle: await self._file_handle.close()

# ================= 5. PostgreSQL 任务管理器 =================
class PGTaskManager:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def init_db(self):
        self.pool = await asyncpg.create_pool(
            dsn=CONFIG.PG_DSN,
            min_size=CONFIG.PG_MIN_SIZE,
            max_size=CONFIG.PG_MAX_SIZE
        )
        
        ddl = """
        CREATE TABLE IF NOT EXISTS tasks (
            url_hash TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            status VARCHAR(20) DEFAULT 'PENDING',
            retry_count INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_status_retry ON tasks(status, retry_count);
        """
        async with self.pool.acquire() as conn:
            await conn.execute(ddl)
        logger.info("🐘 PostgreSQL initialized.")

    def _hash_url(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()

    async def add_tasks(self, urls: List[str]):
        if not urls: return
        query = """
        INSERT INTO tasks (url_hash, url) VALUES ($1, $2)
        ON CONFLICT (url_hash) DO NOTHING
        """
        async with self.pool.acquire() as conn:
            records = [(self._hash_url(u), u) for u in urls]
            await conn.executemany(query, records)

    async def acquire_task(self) -> Optional[Tuple[str, int]]:
        query = f"""
        WITH task_to_process AS (
            SELECT url_hash, url, retry_count
            FROM tasks
            WHERE status = 'PENDING' 
               OR (status = 'FAILED' AND retry_count < $1)
            ORDER BY updated_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE tasks
        SET status = 'PROCESSING', updated_at = NOW()
        FROM task_to_process
        WHERE tasks.url_hash = task_to_process.url_hash
        RETURNING tasks.url, tasks.retry_count;
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query, CONFIG.MAX_RETRIES)
                if row:
                    return row['url'], row['retry_count']
                return None
        except Exception as e:
            logger.error(f"DB Acquire Error: {e}")
            return None

    async def update_task(self, url: str, status: str, fatal: bool = False):
        url_hash = self._hash_url(url)
        query = "UPDATE tasks SET status=$1, updated_at=NOW() WHERE url_hash=$2"
        params = [status, url_hash]
        
        if status == 'FAILED':
            if fatal:
                query = "UPDATE tasks SET status=$1, retry_count=999, updated_at=NOW() WHERE url_hash=$2"
            else:
                query = "UPDATE tasks SET status=$1, retry_count=retry_count+1, updated_at=NOW() WHERE url_hash=$2"
        
        async with self.pool.acquire() as conn:
            await conn.execute(query, *params)

    async def close(self):
        if self.pool: await self.pool.close()

# ================= 6. 网络层 (修复) =================
class NetworkEngine:
    def __init__(self, storage: StorageBackend, db: PGTaskManager, proxy_mgr: ProxyManager):
        self.storage = storage
        self.db = db
        self.proxy_mgr = proxy_mgr
        # 修复：移除 safari17_2，使用更通用的 chrome 指纹
        self.impersonates = ["chrome120", "chrome110", "edge101"]

    async def fetch_and_process(self, url: str) -> Literal["SUCCESS", "RETRY", "FATAL"]:
        proxy = await self.proxy_mgr.get_proxy()
        
        # 如果没有可用代理（全部冷却），等待并返回 RETRY，让 Worker 稍后处理
        if not proxy and CONFIG.PROXY_LIST:
            logger.warning("⏳ No healthy proxies available. Worker sleeping...")
            await asyncio.sleep(5) 
            return "RETRY"

        proxies = {"http": proxy, "https": proxy} if proxy else {}
        
        target_url = url
        is_reddit = "reddit.com" in url
        if is_reddit:
            target_url = RedditHandler.convert_to_api_url(url)

        headers = {"Accept-Language": "en-US,en;q=0.9"}

        try:
            await asyncio.sleep(random.uniform(0.5, 2.0))
            
            async with AsyncSession(
                impersonate=random.choice(self.impersonates),
                proxies=proxies,
                headers=headers,
                timeout=CONFIG.REQUEST_TIMEOUT
            ) as s:
                logger.debug(f"🔍 GET {target_url} | Proxy: {proxy}")
                response = await s.get(target_url)
                
                if response.status_code in [429, 403]:
                    await self.proxy_mgr.report_status(proxy, response.status_code)
                    logger.warning(f"🛡️ Rate Limited/Forbidden ({response.status_code}): {url}. Rotating proxy...")
                    return "RETRY"
                
                if response.status_code == 404:
                    return "FATAL"
                
                if response.status_code != 200:
                    await self.proxy_mgr.report_status(proxy, response.status_code)
                    return "RETRY"

                # 成功
                await self.proxy_mgr.report_status(proxy, 200)

                # 解析逻辑
                if is_reddit:
                    try:
                        json_data = response.json()
                        parsed, new_links = RedditHandler.parse_response(json_data, url)
                        await self.storage.save_data(parsed)
                        if new_links:
                            await self.db.add_tasks(new_links)
                            logger.info(f"🔄 Added {len(new_links)} pagination tasks")
                        return "SUCCESS"
                    except json.JSONDecodeError:
                        return "RETRY"

                content_type = response.headers.get("content-type", "").lower()
                
                if "text/html" in content_type:
                    # 修复：使用 trafilatura.extract 提取正文
                    extracted_text = extract(
                        response.text, 
                        include_comments=False,
                        include_tables=False,
                        no_fallback=False
                    )
                    
                    # 修复：使用 trafilatura.extract_metadata 提取元数据
                    meta = extract_metadata(response.text)
                    title = meta.title if meta else "No Title"
                    
                    if not extracted_text:
                        logger.warning(f"⚠️ Trafilatura returned empty for {url}")
                        
                    data = {
                        "url": url,
                        "type": "html_article",
                        "title": title,
                        "content": extracted_text,
                        "raw_length": len(response.text),
                        "ts": datetime.now().isoformat()
                    }
                    await self.storage.save_data(data)
                    return "SUCCESS"

                return "SUCCESS"

        except RequestsError as e:
            await self.proxy_mgr.report_status(proxy, 0)
            logger.error(f"❌ Network Error {url}: {e}")
            return "RETRY"
        except Exception as e:
            # 捕获其他系统错误 (如库版本问题)，防止 Worker 崩溃
            logger.error(f"❌ System Error {url}: {e}")
            return "RETRY"

# ================= 7. 主编排 =================
class CrawlerEngine:
    def __init__(self):
        self.storage = LocalStorage(CONFIG.DATA_DIR)
        self.db = PGTaskManager()
        self.proxy_mgr = ProxyManager(CONFIG.PROXY_LIST)
        self.network = NetworkEngine(self.storage, self.db, self.proxy_mgr)
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
                logger.info(f"▶️ [{worker_id}] Processing: {url} (Try: {retry_cnt})")
                
                status = await self.network.fetch_and_process(url)
                
                if status == "SUCCESS":
                    await self.db.update_task(url, "COMPLETED")
                elif status == "FATAL":
                    await self.db.update_task(url, "FAILED", fatal=True)
                else: 
                    await self.db.update_task(url, "FAILED", fatal=False)
                    await asyncio.sleep(1) # 失败后稍微冷却
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker Loop Error: {e}")
                await asyncio.sleep(1)

    async def run(self, seeds: List[str]):
        await self.db.init_db()
        await self.storage.initialize()
        await self.db.add_tasks(seeds)
        
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: self.shutdown_event.set())
            except NotImplementedError: pass

        logger.info(f"🚀 Engine Started with {CONFIG.MAX_CONCURRENCY} workers.")
        workers = [asyncio.create_task(self.worker(i)) for i in range(CONFIG.MAX_CONCURRENCY)]
        
        try:
            while not self.shutdown_event.is_set():
                if all(w.done() for w in workers): break
                await asyncio.sleep(1)
        finally:
            self.shutdown_event.set()
            logger.info("⏳ Shutting down...")
            await asyncio.gather(*workers, return_exceptions=True)
            await self.db.close()
            await self.storage.close()
            logger.info("👋 Shutdown Complete.")

if __name__ == "__main__":
    seeds = [
        "https://www.reddit.com/r/SkincareAddictionLux/comments/1kv7g3h/eye_serum_and_cream_recommendations",
    ]
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    engine = CrawlerEngine()
    try:
        asyncio.run(engine.run(seeds))
    except KeyboardInterrupt: pass