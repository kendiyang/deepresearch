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

    # 爬虫行为
    MAX_CONCURRENCY: int = 5       # 稍微降低并发以保证稳定性
    MAX_RETRIES: int = 3
    REQUEST_TIMEOUT: int = 60
    PROXY_LIST: List[str] = [
		"http://5gye4972-region-US-sid-vL9YzAWK-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-ihnrGp32-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-hdJTxtdD-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-SmiDFAL1-t-1:7fsccucj@us.novproxy.io:443",
        "http://5gye4972-region-US-sid-c4CvsW2P-t-1:7fsccucj@us.novproxy.io:443",
	]     # 格式: ["http://user:pass@ip:port"]

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

# ================= 2. 平台专用处理器 (Reddit & TikTok) =================


class RedditHandler:
	"""
	Reddit 最佳实践：将网页 URL 转换为 .json API 请求
	无需解析 HTML，直接获取结构化数据，且极少触发反爬
	"""
	@staticmethod
	def convert_to_api_url(url: str) -> str:
		"""
		将普通 Reddit URL 转换为 JSON API URL，并将 www.reddit.com 替换为 old.reddit.com。
		1. 保留原有的 .json API 转换逻辑。
		2. 强制将主机名 www.reddit.com 替换为 old.reddit.com。
		"""
		parsed = urlparse(url)
		# 替换主机名
		#netloc = parsed.netloc.replace("www.reddit.com", "old.reddit.com")
		# 已经是 .json API 则直接返回（但也要替换主机名）
		if parsed.path.endswith(".json"):
			return urlunparse((
				parsed.scheme,
				parsed.netloc,
				parsed.path,
				parsed.params,
				parsed.query,
				parsed.fragment
			))
		# 去除尾部斜杠并添加 .json
		clean_path = parsed.path.rstrip("/")
		new_path = f"{clean_path}.json"
		# 重组 URL (保留查询参数，如 ?sort=new)
		return urlunparse((
			parsed.scheme,
			parsed.netloc,
			new_path,
			parsed.params,
			parsed.query,
			parsed.fragment
		))

	@staticmethod
	def parse_response(data: Dict, original_url: str) -> Dict:
		"""解析 Reddit JSON 响应"""
		try:
			# Reddit 详情页通常返回一个列表：[PostData, CommentsData]
			# Subreddit 列表页返回一个字典：{kind: "Listing", data: ...}
			content_type = "reddit_listing"
			extracted_data = {}
			if isinstance(data, list):
				content_type = "reddit_post_detail"
				post_info = data[0]['data']['children'][0]['data']
				comments = data[1]['data']['children'] if len(data) > 1 else []
				extracted_data = {
					"title": post_info.get("title"),
					"selftext": post_info.get("selftext"),
					"author": post_info.get("author"),
					"ups": post_info.get("ups"),
					"upvote_ratio": post_info.get("upvote_ratio"),
					"comment_count": post_info.get("num_comments"),
					"created_utc": post_info.get("created_utc"),
					"raw_comments_count": len(comments)
				}
			elif isinstance(data, dict):
				children = data.get("data", {}).get("children", [])
				extracted_data = {
					"post_count": len(children),
					"posts": [c['data'].get('title') for c in children[:5]] # 仅示例
				}
			return {
				"url": original_url,
				"platform": "reddit",
				"type": content_type,
				"parsed": extracted_data,
				"raw_json": data, # 保存完整原始数据
				"timestamp": datetime.now().isoformat()
			}
		except Exception as e:
			return {"url": original_url, "error": str(e), "raw_partial": str(data)[:200]}

class TikTokHandler:
	"""TikTok 数据提取 (基于 Hydration JSON)"""
	@staticmethod
	def extract_data(html: str, url: str) -> Optional[Dict]:
		# 尝试提取 SIGI_STATE 或通用数据
		pattern = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">([^<]+)</script>', html)
		if not pattern:
			pattern = re.search(r'<script id="SIGI_STATE" type="application/json">([^<]+)</script>', html)
		if pattern:
			try:
				raw_data = json.loads(pattern.group(1))
				# 简单解析逻辑 (视具体结构而定)
				return {
					"url": url,
					"platform": "tiktok",
					"type": "video_meta",
					"raw_data": raw_data,
					"timestamp": datetime.now().isoformat()
				}
			except:
				pass
		return None

# ================= 3. 存储抽象层 (Storage Layer) =================

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
			await self._file_handle.flush()

	async def close(self):
		if self._file_handle:
			await self._file_handle.close()

class S3Storage(StorageBackend):
	def __init__(self):
		self.session = get_session()
		self.client = None
		self.bucket = CONFIG.S3_BUCKET_NAME
		self.buffer = []

	async def initialize(self):
		self.client = await self.session.create_client(
			's3', endpoint_url=CONFIG.S3_ENDPOINT_URL,
			aws_access_key_id=CONFIG.S3_ACCESS_KEY,
			aws_secret_access_key=CONFIG.S3_SECRET_KEY,
			region_name=CONFIG.S3_REGION_NAME
		).__aenter__()
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
		if not self.buffer:
			return
		data = list(self.buffer)
		self.buffer.clear()
		key = f"data/{uuid.uuid4().hex}.jsonl"
		body = "\n".join([json.dumps(r, ensure_ascii=False) for r in data]).encode('utf-8')
		await self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

	async def close(self):
		await self._flush()
		if self.client:
			await self.client.__aexit__(None, None, None)

# ================= 4. 数据持久层 (DB) =================
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
				updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
			)
		""")
		await self._db.commit()

	async def add_tasks(self, urls: List[str]):
		if not urls:
			return
		async with self._lock:
			await self._db.executemany(
				"INSERT OR IGNORE INTO tasks (url) VALUES (?)", [(u,) for u in urls]
			)
			await self._db.commit()

	async def acquire_task(self) -> Optional[Tuple[str, int]]:
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
		async with self._lock:
			if status == 'FAILED':
				await self._db.execute("UPDATE tasks SET status=?, retry_count=retry_count+1 WHERE url=?", (status, url))
			else:
				await self._db.execute("UPDATE tasks SET status=? WHERE url=?", (status, url))
			await self._db.commit()

	async def close(self):
		if self._db:
			await self._db.close()

# ================= 5. 网络与解析层 (增强版) =================
class NetworkEngine:
	def __init__(self, storage: StorageBackend):
		self.storage = storage
		# 移除 edge，专注于 chrome/safari 以获得更好兼容性
		self.impersonates = ["chrome120", "safari17_0"]

	def _is_reddit_url(self, url: str) -> bool:
		return "reddit.com" in url

	def _is_tiktok_url(self, url: str) -> bool:
		return "tiktok.com" in url

	async def fetch_and_process(self, url: str) -> bool:
		proxy = random.choice(CONFIG.PROXY_LIST) if CONFIG.PROXY_LIST else None
		proxies = {"http": proxy, "https": proxy} if proxy else {}

		# 针对不同平台调整请求 URL 和 Headers
		target_url = url
		headers = {}

		# Reddit 策略: 转换为 .json
		if self._is_reddit_url(url):
			target_url = RedditHandler.convert_to_api_url(url)
			# Reddit 极其严格地检查 User-Agent
			headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

		try:
			await asyncio.sleep(random.uniform(1.0, 4.0))

			async with AsyncSession(
				impersonate=random.choice(self.impersonates),
				proxies=proxies,
				headers=headers,
				timeout=CONFIG.REQUEST_TIMEOUT
			) as s:
				logger.debug(f"🔍 Requesting: {target_url}")
				response = await s.get(target_url)

				# --- Reddit 特殊处理 ---
				if self._is_reddit_url(url):
					if response.status_code == 200:
						try:
							json_data = response.json()
							parsed = RedditHandler.parse_response(json_data, url)
							await self.storage.save_data(parsed)
							logger.info(f"✅ Saved Reddit JSON: {url}")
							return True
						except json.JSONDecodeError:
							logger.error(f"❌ Reddit returned non-JSON: {url}")
							return False
					else:
						logger.warning(f"⚠️ Reddit Error {response.status_code}: {url}")
						return False

				# --- 错误处理 ---
				if response.status_code != 200:
					logger.warning(f"⚠️ HTTP {response.status_code} - {url}")
					return False

				content_type = response.headers.get("content-type", "").lower()

				# --- TikTok 特殊处理 ---
				if self._is_tiktok_url(url) and "text/html" in content_type:
					tiktok_data = TikTokHandler.extract_data(response.text, url)
					if tiktok_data:
						await self.storage.save_data(tiktok_data)
						logger.info(f"✅ Saved TikTok Meta: {url}")
						return True
					# 如果提取失败，继续保存 HTML 作为后备

				# --- 通用 HTML 处理 ---
				if "text/html" in content_type:
					data = self._parse_html(response.text, url)
					await self.storage.save_data(data)
					logger.info(f"✅ Saved HTML: {url}")
				# --- 通用文件下载 ---
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
		if HAS_TRAFILATURA:
			try:
				content = trafilatura.extract(html, include_comments=False)
			except:
				pass

		if not content:
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

# ================= 6. 主编排 (Orchestrator) =================

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
				task = await self.db.acquire_task()
				if not task:
					await asyncio.sleep(2)
					continue

				url, retry_cnt = task
				logger.info(f"▶️ Worker-{worker_id} processing: {url} (Retry: {retry_cnt})")

				success = await self.network.fetch_and_process(url)

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
		os.makedirs(CONFIG.DATA_DIR, exist_ok=True)
		await self.db.init_db()
		await self.db.add_tasks(seeds)
		await self.storage.initialize()

		loop = asyncio.get_running_loop()
		if sys.platform != "win32":
			try:
				loop.add_signal_handler(signal.SIGINT, lambda: self.shutdown_event.set())
				loop.add_signal_handler(signal.SIGTERM, lambda: self.shutdown_event.set())
			except NotImplementedError:
				pass

		logger.info(f"🚀 Engine Started | Storage: {CONFIG.STORAGE_TYPE} | Workers: {CONFIG.MAX_CONCURRENCY}")

		workers = [asyncio.create_task(self.worker(i)) for i in range(CONFIG.MAX_CONCURRENCY)]

		try:
			while not self.shutdown_event.is_set():
				if all(w.done() for w in workers):
					break
				await asyncio.sleep(1)
		except KeyboardInterrupt:
			logger.info("🛑 Received Stop Signal")
			self.shutdown_event.set()
		finally:
			self.shutdown_event.set()
			logger.info("⏳ Shutting down workers...")
			await asyncio.gather(*workers, return_exceptions=True)
			await self.db.close()
			await self.storage.close()
			logger.info("👋 Shutdown Complete.")

if __name__ == "__main__":
	# 混合测试种子：包含 PubMed (通用), TikTok (正则提取), Reddit (JSON API)
	targets = [
		# --- Reddit 测试 (会自动转为 JSON API) ---
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
	]

	if sys.platform == 'win32':
		asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

	engine = CrawlerEngine()
	try:
		asyncio.run(engine.run(targets))
	except KeyboardInterrupt:
		pass