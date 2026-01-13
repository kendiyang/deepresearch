import asyncio
import logging
import json
import random
import re
import os
import hashlib
import mimetypes
import aiofiles
from typing import List, Dict, Optional, Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

# --- 第三方库 ---
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

# 尝试导入 trafilatura
try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

# ================= 配置区域 =================
OUTPUT_FILE = "scraped_data.jsonl"
DOWNLOAD_DIR = "downloads"  # 新增：文件保存目录
PROXY_LIST = []             # 代理列表 ["http://u:p@ip:port"]
MAX_CONCURRENCY = 3
# ===========================================

# 自动创建下载目录
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("Scraper")

class ProxyManager:
    def __init__(self, proxies: List[str]):
        self.proxies = proxies
    
    def get_proxy(self) -> Optional[str]:
        return random.choice(self.proxies) if self.proxies else None

class UniversalParser:
    """通用解析器：负责从 HTML 中提取结构化数据"""
    
    def _clean_text(self, text: str) -> str:
        if not text: return ""
        return re.sub(r'\s+', ' ', text).strip()

    def _extract_nextjs_data(self, soup: BeautifulSoup) -> Dict:
        script = soup.find("script", id="__NEXT_DATA__", type="application/json")
        if script:
            try: return json.loads(script.string)
            except: pass
        return {}

    def _recursive_find(self, data: Any, target_keys: List[str], results: List[Any]):
        if isinstance(data, dict):
            match = True
            for k in target_keys:
                if k not in data:
                    match = False
                    break
            if match: results.append(data)
            for v in data.values(): self._recursive_find(v, target_keys, results)
        elif isinstance(data, list):
            for item in data: self._recursive_find(item, target_keys, results)

    def parse(self, html: str, url: str) -> Dict:
        soup = BeautifulSoup(html, "html.parser")
        domain = urlparse(url).netloc
        
        result = {
            "url": url,
            "domain": domain,
            "type": "generic",
            "title": "",
            "content": "",
            "reviews": [],
            "total_pages": 0
        }

        # 提取标题
        if soup.title: result["title"] = self._clean_text(soup.title.string)

        # 判断是否为 Trustpilot
        if "trustpilot.com" in domain:
            result["type"] = "review_platform"
            next_data = self._extract_nextjs_data(soup)
            if next_data:
                # 提取评论
                raw_reviews = []
                self._recursive_find(next_data, ["reviewText", "rating"], raw_reviews)
                if not raw_reviews:
                    self._recursive_find(next_data, ["text", "rating"], raw_reviews)
                
                for item in raw_reviews:
                    text = item.get("reviewText") or item.get("text")
                    if text:
                        result["reviews"].append({
                            "rating": item.get("rating"),
                            "text": text,
                            "date": item.get("dates", {}).get("publishedDate")
                        })
                
                # 提取分页
                found_pages = []
                def find_key(obj, key):
                    if isinstance(obj, dict):
                        if key in obj: found_pages.append(obj[key])
                        for v in obj.values(): find_key(v, key)
                    elif isinstance(obj, list):
                        for i in obj: find_key(i, key)
                find_key(next_data, "totalPages")
                if found_pages:
                    vals = [int(x) for x in found_pages if str(x).isdigit()]
                    if vals: result["total_pages"] = max(vals)

        else:
            # 通用网页
            result["type"] = "article/general"
            if HAS_TRAFILATURA:
                extracted = trafilatura.extract(html, include_comments=False)
                if extracted: result["content"] = extracted
            
            if not result["content"]:
                paras = [self._clean_text(p.get_text()) for p in soup.find_all("p")]
                result["content"] = "\n\n".join([p for p in paras if len(p) > 30])

        return result

class StealthScraper:
    """下载器：支持 HTML 解析与文件下载"""
    def __init__(self, proxy_manager: ProxyManager):
        self.proxy_manager = proxy_manager
        self.impersonates = ["chrome120", "safari17_0", "chrome110"]
        self.parser = UniversalParser()

    def _get_filename(self, url: str, content_type: str) -> str:
        """直接用 URL 最后一级文件名命名，如无则用 md5+扩展名兜底"""
        parsed = urlparse(url)
        path = parsed.path
        basename = os.path.basename(path)
        ext = os.path.splitext(basename)[1]
        # 如果 basename 存在且有扩展名，直接用
        if basename and ext:
            return basename
        # 否则用 md5+扩展名兜底
        ext_guess = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if not ext_guess:
            ext_guess = ".bin"
        file_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
        return f"{file_hash}{ext_guess}"

    async def _save_binary(self, content: bytes, url: str, content_type: str) -> Dict:
        """保存二进制文件到本地"""
        filename = self._get_filename(url, content_type)
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        async with aiofiles.open(filepath, 'wb') as f:
            await f.write(content)
        
        logger.info(f"💾 文件已保存: {filename} ({len(content)/1024:.1f} KB)")
        
        return {
            "url": url,
            "type": "file",
            "file_path": filepath,
            "content_type": content_type,
            "size_bytes": len(content)
        }

    async def fetch_and_process(self, url: str) -> Optional[Dict]:
        """核心方法：请求 URL 并根据类型决定是解析还是下载"""
        for attempt in range(3):
            proxy = self.proxy_manager.get_proxy()
            impersonate_ver = random.choice(self.impersonates)
            
            try:
                await asyncio.sleep(random.uniform(1, 3))
                
                async with AsyncSession(
                    impersonate=impersonate_ver,
                    headers={"Referer": "https://www.google.com/"},
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    timeout=45 # 下载文件可能需要更长时间
                ) as session:
                    response = await session.get(url)
                    
                    if response.status_code == 200:
                        content_type = response.headers.get("content-type", "").lower()
                        
                        # --- 分支逻辑：文件 vs 网页 ---
                        
                        # 1. 如果是常见的文本格式，进行解析
                        if "text/html" in content_type or "application/json" in content_type:
                            return self.parser.parse(response.text, url)
                        
                        # 2. 否则视为文件 (PDF, Image, Zip, etc.) 进行下载
                        else:
                            return await self._save_binary(response.content, url, content_type)

                    elif response.status_code in [403, 429]:
                        logger.warning(f"🚫 [{response.status_code}] Retry: {url}")
                        continue
                    elif response.status_code == 404:
                        return None
            
            except Exception as e:
                logger.error(f"❌ Error {url}: {str(e)[:50]}")
                await asyncio.sleep(1)
        
        return None

class CrawlerManager:
    def __init__(self, start_urls: List[str]):
        self.start_urls = start_urls
        self.queue = asyncio.Queue()
        self.seen_urls = set()
        self.scraper = StealthScraper(ProxyManager(PROXY_LIST))

    def _generate_pagination(self, base_url: str, total_pages: int) -> List[str]:
        parsed = urlparse(base_url)
        qs = parse_qs(parsed.query)
        qs.pop('page', None)
        links = []
        for p in range(2, total_pages + 1):
            qs['page'] = [str(p)]
            new_query = urlencode(qs, doseq=True)
            new_url = urlunparse(parsed._replace(query=new_query))
            links.append(new_url)
        return links

    async def worker(self, worker_id: int):
        async with aiofiles.open(OUTPUT_FILE, mode='a', encoding='utf-8') as f:
            while True:
                try:
                    url = await asyncio.wait_for(self.queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    if self.queue.empty(): break
                    continue

                if url in self.seen_urls:
                    self.queue.task_done()
                    continue
                self.seen_urls.add(url)

                logger.info(f"👷 [Worker-{worker_id}] 任务: {url}")
                data = await self.scraper.fetch_and_process(url)

                if data:
                    # 构建保存记录
                    save_data = {
                        "url": data["url"],
                        "type": data["type"],
                        "timestamp": asyncio.get_event_loop().time()
                    }

                    # 根据返回类型分别处理
                    if data["type"] == "file":
                        save_data["local_path"] = data["file_path"]
                        save_data["content_type"] = data["content_type"]
                        save_data["file_size"] = data["size_bytes"]
                    
                    elif data["type"] == "review_platform":
                        save_data["title"] = data["title"]
                        save_data["reviews"] = data["reviews"]
                        # 处理 Trustpilot 翻页
                        if data.get("total_pages", 0) > 1 and ("page=" not in url or "page=1" in url):
                            logger.info(f"✨ 发现 {data['total_pages']} 页，生成任务...")
                            new_links = self._generate_pagination(url, data["total_pages"])
                            for link in new_links:
                                if link not in self.seen_urls: await self.queue.put(link)
                    
                    else: # generic article
                        save_data["title"] = data["title"]
                        save_data["content"] = data["content"]

                    await f.write(json.dumps(save_data, ensure_ascii=False) + "\n")

                self.queue.task_done()

    async def run(self):
        for url in self.start_urls: await self.queue.put(url)
        workers = [asyncio.create_task(self.worker(i)) for i in range(MAX_CONCURRENCY)]
        await self.queue.join()
        for w in workers: w.cancel()
        logger.info(f"🎉 全部完成。数据已保存至 {OUTPUT_FILE}，文件已保存至 {DOWNLOAD_DIR}/")

if __name__ == "__main__":
    targets   = [
    "https://ca.trustpilot.com/review/musely.com?page=2"
]


    try:
        import sys
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except ImportError:
        pass

    crawler = CrawlerManager(targets)
    asyncio.run(crawler.run())