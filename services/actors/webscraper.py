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
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Literal
from urllib.parse import urlparse

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

# ================= 1. 深度配置层 (Configuration) =================
class AppConfig(BaseSettings):
    APP_NAME: str = "HybridScraper"
    DATA_DIR: str = "./data"
    DB_NAME: str = "task_queue.db"
    
    STORAGE_TYPE: Literal["local", "s3"] = Field(default="local")
    
    # S3 / MinIO (可选)
    S3_ENDPOINT_URL: Optional[str] = "http://localhost:9000"
    S3_ACCESS_KEY: Optional[str] = "minioadmin"
    S3_SECRET_KEY: Optional[str] = "minioadmin"
    S3_BUCKET_NAME: str = "crawler-data"
    S3_REGION_NAME: str = "us-east-1"
    S3_BATCH_SIZE: int = 50
    S3_FLUSH_INTERVAL: int = 30
    
    # 爬虫行为
    MAX_CONCURRENCY: int = 10      # 建议先从3开始测试，稳定后再加
    MAX_RETRIES: int = 3
    REQUEST_TIMEOUT: int = 45     # 增加超时时间以适应慢速代理
    PROXY_LIST: List[str] = []    # 格式: ["http://user:pass@ip:port"]

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

# ================= 2. 存储抽象层 (Storage Layer) =================

class StorageBackend(ABC):
    @abstractmethod
    async def initialize(self): pass
    @abstractmethod
    async def save_asset(self, content: bytes, filename: str, content_type: str) -> str: pass
    @abstractmethod
    async def save_data(self, record: Dict): pass
    @abstractmethod
    async def close(self): pass

class LocalStorage(StorageBackend):
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.downloads_dir = os.path.join(base_dir, "downloads")
        self.data_file = os.path.join(base_dir, "scraped_data.jsonl")
        self._file_handle = None

    async def initialize(self):
        os.makedirs(self.downloads_dir, exist_ok=True)
        # 使用 append 模式，且 buffering=1 确保尽快写入
        self._file_handle = await aiofiles.open(self.data_file, mode='a', encoding='utf-8', buffering=1)
        logger.info(f"💾 Local Storage initialized at {self.base_dir}")

    async def save_asset(self, content: bytes, filename: str, content_type: str) -> str:
        filepath = os.path.join(self.downloads_dir, filename)
        async with aiofiles.open(filepath, 'wb') as f:
            await f.write(content)
        return filepath

    async def save_data(self, record: Dict):
        if self._file_handle:
            await self._file_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            # 强制刷新缓冲区，防止程序意外退出数据丢失
            await self._file_handle.flush()

    async def close(self):
        if self._file_handle:
            await self._file_handle.close()
            logger.info("💾 Local Storage closed")

class S3Storage(StorageBackend):
    # (S3代码保持不变，为节省篇幅略去，逻辑同上)
    def __init__(self):
        self.session = get_session()
        self.client = None
        self.bucket = CONFIG.S3_BUCKET_NAME
        self.buffer = []
        self._bg_task = None
        self._running = False

    async def initialize(self):
        self.client = await self.session.create_client(
            's3', endpoint_url=CONFIG.S3_ENDPOINT_URL,
            aws_access_key_id=CONFIG.S3_ACCESS_KEY,
            aws_secret_access_key=CONFIG.S3_SECRET_KEY,
            region_name=CONFIG.S3_REGION_NAME
        ).__aenter__()
        self._running = True
        logger.info("☁️ S3 Storage initialized")

    async def save_asset(self, content: bytes, filename: str, content_type: str) -> str:
        key = f"assets/{filename}"
        await self.client.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType=content_type)
        return f"s3://{self.bucket}/{key}"

    async def save_data(self, record: Dict):
        self.buffer.append(record)
        if len(self.buffer) >= CONFIG.S3_BATCH_SIZE:
            await self._flush()

    async def _flush(self):
        if not self.buffer: return
        data = list(self.buffer)
        self.buffer.clear()
        key = f"data/{uuid.uuid4().hex}.jsonl"
        body = "\n".join([json.dumps(r, ensure_ascii=False) for r in data]).encode('utf-8')
        await self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

    async def close(self):
        await self._flush()
        if self.client: await self.client.__aexit__(None, None, None)

# ================= 3. 数据持久层 (DB - 任务队列) =================
class TaskManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db = None
        self._lock = asyncio.Lock()

    async def init_db(self):
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL;")  # 关键：开启 WAL 模式以支持并发读写
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                url TEXT PRIMARY KEY,
                status TEXT DEFAULT 'PENDING',
                retry_count INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await self._db.commit()


    async def add_tasks(self, urls: List[str]):
        if not urls: return
        # 使用 INSERT OR IGNORE 避免重复添加
        async with self._lock:
            await self._db.executemany(
                "INSERT OR IGNORE INTO tasks (url) VALUES (?)", [(u,) for u in urls]
            )
            await self._db.commit()

    async def acquire_task(self) -> Optional[Tuple[str, int]]:
        """
        核心修复：
        1. 使用 RETURNING 子句原子性获取并更新任务
        2. 确保 commit 在 cursor 上下文之外
        """
        q = f"""
            UPDATE tasks
            SET status = 'PROCESSING', updated_at = CURRENT_TIMESTAMP
            WHERE url = (
                SELECT url FROM tasks 
                WHERE status = 'PENDING' 
                OR (status = 'FAILED' AND retry_count < ?)
                LIMIT 1
            )
            RETURNING url, retry_count
        """

        async with self._lock:
            try:
                row = None
                async with self._db.execute(q, (CONFIG.MAX_RETRIES,)) as cursor:
                    row = await cursor.fetchone()

                if row:
                    await self._db.commit()
                    return row

                return None

            except Exception as e:
                logger.error(f"DB Acquire Error: {e}")
                return None

    async def update_task(self, url: str, status: str):
        try:
            async with self._lock:
                if status == 'FAILED':
                    await self._db.execute("UPDATE tasks SET status=?, retry_count=retry_count+1 WHERE url=?", (status, url))
                else:
                    await self._db.execute("UPDATE tasks SET status=? WHERE url=?", (status, url))
                await self._db.commit()
        except Exception as e:
            logger.error(f"DB Update Error for {url}: {e}")

    async def close(self):
        if self._db: await self._db.close()

# ================= 4. 网络与解析层 (核心修复) =================
class NetworkEngine:
    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self.impersonates = ["chrome120", "safari17_0", "edge101"]

    async def fetch_and_process(self, url: str) -> bool:
        proxy = random.choice(CONFIG.PROXY_LIST) if CONFIG.PROXY_LIST else None
        
        try:
            # 1. 随机延迟
            await asyncio.sleep(random.uniform(1.0, 3.0))
            
            # 2. 创建 Session (关键修复：移除 asyncio.wait_for)
            # curl_cffi 的 timeout 参数已经足够，外部 wait_for 容易引起 C 层死锁
            # 明确传递 proxies：当没有配置代理时传空 dict，避免 libcurl 从环境变量读取无效代理
            proxies = {"http": proxy, "https": proxy} if proxy else {}

            async with AsyncSession(
                impersonate=random.choice(self.impersonates),
                proxies=proxies,
                timeout=CONFIG.REQUEST_TIMEOUT
            ) as s:
                
                logger.debug(f"🔍 Requesting: {url}")
                response = await s.get(url)
                
                # 3. 状态码判断
                if response.status_code != 200:
                    logger.warning(f"⚠️ HTTP {response.status_code} - {url}")
                    return False
                
                # 4. 内容处理
                content_type = response.headers.get("content-type", "").lower()
                
                if "text/html" in content_type:
                    data = self._parse_html(response.text, url)
                    await self.storage.save_data(data)
                    logger.info(f"✅ Saved HTML: {url}")
                else:
                    filename = self._get_filename(url, content_type)
                    path = await self.storage.save_asset(response.content, filename, content_type)
                    meta = {
                        "url": url,
                        "type": "asset",
                        "path": path,
                        "size": len(response.content),
                        "ts": datetime.now().isoformat()
                    }
                    await self.storage.save_data(meta)
                    logger.info(f"✅ Saved Asset: {filename}")
                
                return True

        except RequestsError as e:
            logger.error(f"❌ Network Error {url}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ System Error {url}: {e}")
            return False

    def _parse_html(self, html: str, url: str) -> Dict:
        soup = BeautifulSoup(html, "html.parser")
        content = ""
        # 优先使用 Trafilatura，否则降级到 BS4
        if HAS_TRAFILATURA:
            try:
                content = trafilatura.extract(html, include_comments=False)
            except: pass
            
        if not content:
            # 简单降级处理
            paras = [p.get_text().strip() for p in soup.find_all("p")]
            content = "\n".join([p for p in paras if len(p) > 20])

        title = soup.title.string.strip() if soup.title else "No Title"
        
        return {
            "url": url,
            "type": "html",
            "title": title,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }

    def _get_filename(self, url: str, content_type: str) -> str:
        parsed = urlparse(url)
        path = parsed.path
        name = os.path.basename(path)
        if not name or "." not in name:
            ext = mimetypes.guess_extension(content_type.split(';')[0]) or ".bin"
            hash_name = hashlib.md5(url.encode()).hexdigest()
            name = f"{hash_name}{ext}"
        return name

# ================= 5. 主编排 (Orchestrator) =================
class CrawlerEngine:
    def __init__(self):
        if CONFIG.STORAGE_TYPE == "s3":
            self.storage = S3Storage()
        else:
            self.storage = LocalStorage(CONFIG.DATA_DIR)
            
        self.db = TaskManager(os.path.join(CONFIG.DATA_DIR, CONFIG.DB_NAME))
        self.network = NetworkEngine(self.storage)
        self.shutdown_event = asyncio.Event()

    async def worker(self, worker_id: int):
        logger.info(f"👷 Worker-{worker_id} ready")
        while not self.shutdown_event.is_set():
            try:
                # 获取任务
                task = await self.db.acquire_task()
                
                if not task:
                    # 队列空时稍微休息，避免死循环消耗CPU
                    await asyncio.sleep(2)
                    continue
                
                url, retry_cnt = task
                logger.info(f"▶️ Worker-{worker_id} processing: {url} (Retry: {retry_cnt})")
                
                # 执行下载
                success = await self.network.fetch_and_process(url)
                
                # 更新状态
                if success:
                    await self.db.update_task(url, "COMPLETED")
                else:
                    await self.db.update_task(url, "FAILED")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker-{worker_id} Loop Error: {e}")
                await asyncio.sleep(1)

    async def run(self, seeds: List[str]):
        # 初始化目录
        os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
        
        # 初始化组件
        await self.db.init_db()
        await self.db.add_tasks(seeds)
        await self.storage.initialize()
        
        # 信号处理 (Windows 下忽略)
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            try:
                loop.add_signal_handler(signal.SIGINT, lambda: self.shutdown_event.set())
                loop.add_signal_handler(signal.SIGTERM, lambda: self.shutdown_event.set())
            except NotImplementedError:
                pass

        logger.info(f"🚀 Engine Started | Storage: {CONFIG.STORAGE_TYPE} | Workers: {CONFIG.MAX_CONCURRENCY}")
        
        # 启动 Workers
        workers = [asyncio.create_task(self.worker(i)) for i in range(CONFIG.MAX_CONCURRENCY)]
        
        try:
            # 主循环监控
            while not self.shutdown_event.is_set():
                # 检查所有 worker 是否存活
                if all(w.done() for w in workers):
                    logger.info("All workers finished unexpectedly.")
                    break
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            logger.info("🛑 Received Stop Signal")
            self.shutdown_event.set()
        finally:
            self.shutdown_event.set()
            logger.info("⏳ Shutting down workers...")
            # 等待所有 Worker 结束
            await asyncio.gather(*workers, return_exceptions=True)
            
            # 关闭资源
            await self.db.close()
            await self.storage.close()
            logger.info("👋 Shutdown Complete.")

if __name__ == "__main__":
    # 种子URL
    targets = ["https://pubmed.ncbi.nlm.nih.gov/40927190", "https://pubmed.ncbi.nlm.nih.gov/39875757", "https://pubmed.ncbi.nlm.nih.gov/40733062", "https://pubmed.ncbi.nlm.nih.gov/40711084", "https://pubmed.ncbi.nlm.nih.gov/41341032", "https://pubmed.ncbi.nlm.nih.gov/41075523", "https://pubmed.ncbi.nlm.nih.gov/41037121", "https://pubmed.ncbi.nlm.nih.gov/40810753", "https://pubmed.ncbi.nlm.nih.gov/40997946", "https://pubmed.ncbi.nlm.nih.gov/40690118", "https://pubmed.ncbi.nlm.nih.gov/40074996", "https://pubmed.ncbi.nlm.nih.gov/40488034", "https://pubmed.ncbi.nlm.nih.gov/40667507", "https://pmc.ncbi.nlm.nih.gov/articles/PMC12669112", "https://www.researchgate.net/figure/Production-of-frankincense-nutraceuticals-Boswellia-tree-grown-in-Somalia-a_fig1_336222354", "https://www.trustpilot.com/review/vedaoils.com", "https://www.trustpilot.com/review/wholesalebotanics.com", "https://www.trustpilot.com/review/freshskin.co.uk?page=4", "https://www.trustpilot.com/review/www.planttherapy.com", "https://www.trustpilot.com/review/youngliving.com", "https://au.trustpilot.com/review/www.thearomatherapyshop.com", "https://www.trustpilot.com/review/doterra.com?page=2", "https://www.trustpilot.com/review/majesticpure.com", "https://uk.trustpilot.com/review/freshskin.co.uk", "https://www.trustpilot.com/review/www.edensgarden.com", "https://www.trendhunter.com/slideshow/november-2025-cosmetics", "https://pubmed.ncbi.nlm.nih.gov/40143183", "https://ca.trustpilot.com/review/www.vitalityextracts.com?page=5", "https://www.trustpilot.com/review/www.vitalityextracts.com?page=4", "https://www.trustpilot.com/review/www.planttherapy.com?page=2", "https://www.trustpilot.com/review/fiercenature.co.uk", "https://uk.trustpilot.com/review/fiercenature.co.uk?page=3", "https://www.trustpilot.com/review/beecosmetics.co.uk", "https://ie.trustpilot.com/review/fiercenature.co.uk?page=4", "https://ca.trustpilot.com/review/fiercenature.co.uk?page=7", "https://www.trustpilot.com/review/www.kjserums.com", "https://www.trustpilot.com/review/www.peterthomasroth.com?page=4", "https://www.trustpilot.com/review/skinbunny.co.uk?page=3", "https://www.trustpilot.com/review/musely.com?page=9", "https://uk.trustpilot.com/review/www.peterthomasroth.com?page=3", "https://au.trustpilot.com/review/www.peterthomasroth.com?page=2", "https://www.trustpilot.com/review/amoils.com", "https://www.trustpilot.com/review/hudabeauty.com", "https://www.trustpilot.com/review/uklash.com?page=2", "https://www.trustpilot.com/review/eelhoe.us?page=2", "https://pubmed.ncbi.nlm.nih.gov/40944206", "https://www.trustpilot.com/review/romemd.com", "https://www.trustpilot.com/review/elireskincare.com?page=2", "https://ie.trustpilot.com/review/romemd.com?page=2", "https://ie.trustpilot.com/review/skinphysics.com.au", "https://www.trustpilot.com/review/www.ellessence.co.uk?page=2", "https://www.trustpilot.com/review/harmonyclinic.online", "https://www.trustpilot.com/review/skinphysics.com.au?page=5", "https://ie.trustpilot.com/review/doctorgskincare.com?page=5", "https://www.trustpilot.com/review/lustralotions.com", "https://uk.trustpilot.com/review/www.cowfacebeauty.com", "https://www.vogue.com/article/the-vogue-business-beauty-tracker", "https://www.vogue.com/article/best-serum-for-wrinkles", "https://www.vogue.com/sponsored/article/luxury-skincare-brand-sulwhasoo-celebrates-the-power-of-korean-ginseng-in-new-york", "https://www.vogue.com/article/hydrating-toners", "https://www.vogue.com/article/best-skincare-women-over-50", "https://www.vogue.com/article/best-body-oils", "https://www.vogue.com/article/best-face-mask-any-skin-type", "https://www.vogue.com/article/best-skincare-for-rosacea", "https://www.vogue.com/video/watch/best-of-beauty-secrets-2025", "https://www.vogue.com/article/best-moisturizer-for-oily-skin"]
    
    # Windows 兼容性设置 (如果是在 Windows 运行)
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    engine = CrawlerEngine()
    try:
        asyncio.run(engine.run(targets))
    except KeyboardInterrupt:
        pass