import asyncio
import logging
import json
import random
import re
from typing import List, Dict, Optional
from urllib.parse import urlparse
from services.actors.webrules import SITE_CONFIGS

# 第三方库
from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession

# --- 1. 日志与全局配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)



# --- 3. 核心抓取器 (隐身增强版) ---
class StealthScraper:
    def __init__(self, concurrency: int = 3, timeout: int = 30):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        # 移除了 edge99 (版本过老容易被识别)，保留主流版本
        self.impersonate_list = ["chrome120", "safari17_0", "chrome110"]

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def _get_headers(self, impersonate_ver: str) -> Dict:
        """
        优化：让 curl_cffi 自动处理 Sec-Ch-Ua 等底层指纹。
        只补充应用层必要的 Headers。
        """
        headers = {
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Referer": "https://www.google.com/", # 模拟从 Google 搜索进入
        }
        return headers

    def _clean_text(self, text: str) -> str:
        if not text: return ""
        # 移除不可见字符但保留空格
        text = text.replace('\xa0', ' ')
        # 将多余的空白字符折叠成一个空格
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _parse_html(self, html: str, final_url: str) -> Dict:
        soup = BeautifulSoup(html, "html.parser")
        
        # 0. 全局清洗：移除干扰元素
        for tag in soup(["script", "style", "nav", "footer", "iframe", "svg", "noscript", "form", "header"]):
            tag.decompose()

        # 1. 匹配配置
        config = None
        domain = urlparse(final_url).netloc
        for cfg in SITE_CONFIGS:
            if cfg.domain_keyword in domain:
                config = cfg
                break
        
        data = {
            "url": final_url,
            "domain": domain,
            "title": "",
            "content": "",
            "method": "generic"
        }

        # 2. 规则提取
        if config:
            data["method"] = f"rule:{config.domain_keyword}"
            # 移除特定选择器
            for sel in config.remove_selectors:
                for tag in soup.select(sel): tag.decompose()

            # 提取标题
            title_tag = soup.select_one(config.title_selector)
            if title_tag:
                data["title"] = self._clean_text(title_tag.get_text())

            # 提取内容
            if config.is_medium_style:
                 article_tag = soup.select_one(config.content_selector)
                 if article_tag:
                     # 针对 Medium 类结构，抓取所有层级的文本标签
                     elements = article_tag.find_all(['p', 'h2', 'h3', 'blockquote', 'li'])
                     texts = [self._clean_text(el.get_text()) for el in elements]
                     data["content"] = "\n\n".join([t for t in texts if len(t) > 5])
            else:
                content_tags = soup.select(config.content_selector)
                parts = []
                for tag in content_tags:
                    # 递归获取文本，并过滤极短的噪音
                    clean_txt = self._clean_text(tag.get_text())
                    if len(clean_txt) > 20: 
                        parts.append(clean_txt)
                data["content"] = "\n\n".join(parts)

        # 3. 兜底策略 (Generic Fallback)
        if not data["title"]:
            data["title"] = self._clean_text(soup.title.string if soup.title else "")
        
        if not data["content"]:
            data["method"] = "fallback"
            # 智能提取段落：过滤掉导航栏文字，只保留长段落
            paras = [self._clean_text(p.get_text()) for p in soup.find_all("p")]
            # 简单的正文密度算法：保留长度超过 50 字符的段落
            data["content"] = "\n\n".join([p for p in paras if len(p) > 50])

        return data

    async def scrape_url(self, url: str) -> Optional[Dict]:
        async with self.semaphore:
            for impersonate_ver in self.impersonate_list:
                try:
                    # 随机延迟，模拟人类行为
                    await asyncio.sleep(random.uniform(0.5, 2.0))
                    
                    headers = self._get_headers(impersonate_ver)
                    
                    # 关键：impersonate 参数会自动处理 TLS 指纹
                    async with AsyncSession(
                        impersonate=impersonate_ver, 
                        headers=headers,
                        timeout=self.timeout,
                        proxies=None,
                        allow_redirects=True
                    ) as session:
                        logger.info(f"Fetching: {url} | Fingerprint: {impersonate_ver}")
                        response = await session.get(url)
                        final_url = str(response.url)

                        # 检查是否被重定向到登录页
                        if "/login" in final_url.lower() or "/signin" in final_url.lower():
                            if "/login" not in url.lower():
                                logger.warning(f"⚠️ Login Wall detected: {final_url}")
                                continue

                        if response.status_code == 200:
                            # 简单的反爬检查：有些网站返回 200 但内容是 "Access Denied"
                            if "captcha" in response.text.lower() and len(response.text) < 5000:
                                logger.warning(f"⚠️ Captcha detected via content check.")
                                continue
                            return self._parse_html(response.text, final_url)
                        
                        elif response.status_code in [403, 401, 503]:
                            logger.warning(f"🚫 {response.status_code} Blocked. Switching fingerprint...")
                            continue 
                        else:
                            logger.error(f"Failed {response.status_code}: {final_url}")
                
                except Exception as e:
                    logger.error(f"Error fetching {url}: {str(e)[:100]}") # 限制错误日志长度
                    continue
            
            logger.error(f"❌ Failed all attempts for {url}")
            return None

# --- 4. 运行入口 ---
async def main():
    # 测试链接列表
    target_urls = [
        "https://ca.trustpilot.com/review/strivectin.com"
    ]

    results = []
    
    async with StealthScraper(concurrency=3) as scraper:
        tasks = [scraper.scrape_url(url) for url in target_urls]
        # 使用 tqdm 或简单的计数器可以增加进度条体验（这里保持简单）
        for f in asyncio.as_completed(tasks):
            res = await f
            if res:
                results.append(res)
                # 打印预览
                print(f"\n✅ [{res['domain']}] {res['title'][:50]}...")
                print(f"   Mode: {res['method']} | Content Len: {len(res['content'])}")

    # 保存结果
    with open("final_result.jsonl", "w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    logger.info(f"Done. Saved {len(results)} records.")

if __name__ == "__main__":
    # Windows 下 asyncio 的事件循环兼容性设置
    try:
        import sys
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except ImportError:
        pass
    asyncio.run(main())