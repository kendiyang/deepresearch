import asyncio
import logging
import json
import random
import re
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

# 关键库：模拟真实浏览器 TLS 指纹
from curl_cffi.requests import AsyncSession

# --- 1. 日志与全局配置 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --- 2. 站点配置 (保留之前逻辑) ---
@dataclass
class SiteConfig:
    domain_keyword: str
    title_selector: str
    content_selector: str 
    remove_selectors: List[str] = field(default_factory=list)
    is_medium_style: bool = False 

# 配置表 (精简展示，逻辑通用)
# --- 2. 站点规则库 (新增商业、咨询、博客、众筹) ---
SITE_CONFIGS = [
    # ==================== 商业新闻与媒体 ====================
    SiteConfig(
        domain_keyword="reuters.com",
        title_selector="h1", 
        # Reuters 结构常变，但 article 标签通常稳定
        content_selector="article, .article-body__content__17Yit", 
        remove_selectors=["div[class*='masked-content']", "div[data-testid='behaviors-container']"]
    ),
    SiteConfig(
        domain_keyword="techcrunch.com",
        title_selector="h1",
        content_selector=".wp-block-post-content", # 也就是 entry-content
        remove_selectors=[".wp-block-tc-tcr-related-articles-list"]
    ),
    SiteConfig(
        domain_keyword="businessinsider.com",
        title_selector="h1",
        content_selector=".news-content, article",
        remove_selectors=[".piano-verify-container", ".l-market-ticker"]
    ),
    SiteConfig(
        domain_keyword="fastcompany.com",
        title_selector="h1",
        content_selector="article, .post-content",
        remove_selectors=[".advertisement"]
    ),
    SiteConfig(
        domain_keyword="vogue.com",
        title_selector="h1",
        content_selector=".body__inner-container, .article-body",
        remove_selectors=[".c-gallery-vertical"] # 移除图片画廊，只抓文字
    ),

    # ==================== 咨询公司洞察 (报告与文章) ====================
    SiteConfig(
        domain_keyword="mckinsey.com",
        title_selector="h1",
        # McKinsey 常用 ID 或 specific class
        content_selector="#main-content .mck-c-text-editorial, .mdc-u-grid-container",
        remove_selectors=[".mck-c-expandable-list", ".gov-signin"]
    ),
    SiteConfig(
        domain_keyword="bcg.com",
        title_selector="h1",
        content_selector=".main-content", 
        remove_selectors=[".bcg-share-bar", ".related-content"]
    ),
    SiteConfig(
        domain_keyword="bain.com",
        title_selector="h1",
        content_selector=".article-content",
        remove_selectors=[".share-bar", ".author-bio"]
    ),

    # ==================== 博客平台 (难点) ====================
    # Medium: 类名全是乱码 (e.g. class="ab c d")
    # 策略: 开启 is_medium_style，只抓取 article 下的 p/h2/h3，忽略类名
    SiteConfig(
        domain_keyword="medium.com",
        title_selector="h1", # 即使类名乱码，H1 标签通常只有一个
        content_selector="article", # 抓整个 article
        remove_selectors=[".speechify-controls", "button"],
        is_medium_style=True
    ),
    SiteConfig(
        domain_keyword="substack.com",
        title_selector="h1.post-title, h1",
        content_selector=".body.markup, .post-body",
        remove_selectors=[".subscribe-widget", ".share-dialog"]
    ),

    # ==================== 众筹平台 ====================
    SiteConfig(
        domain_keyword="kickstarter.com",
        title_selector=".project-name, h2.title",
        # 抓取 "Story" 栏目和 "Risks" 栏目
        content_selector=".story-content, #risks-and-challenges",
        remove_selectors=[".video-player", ".msg-overlay"]
    ),

    # 修正后的 MDPI 配置
   SiteConfig(
        domain_keyword="mdpi.com",
        title_selector="h1",
        # 优先抓取 article 标签 (对应您的截图)
        # 如果是旧版页面，则回退抓取 .art-abstract 或 .html-abstract
        content_selector="article, div.art-abstract, div.html-abstract, section.abstract", 
        
        # 移除正文中的干扰项 (参考文献、作者信息、按钮、推荐阅读等)
        remove_selectors=[
            ".art-authors", 
            ".art-affiliations", 
            ".bib-identity", 
            "button", 
            ".related-articles",
            ".back-to-top"
        ]
    )
]

# --- 3. 核心抓取器 (基于 curl_cffi) ---
class BrowserScraper:
    def __init__(self, concurrency: int = 3, timeout: int = 30):
        self.semaphore = asyncio.Semaphore(concurrency)
        self.timeout = timeout
        self.session = None

    async def __aenter__(self):
        # 初始化模拟 Session
        # impersonate="chrome120" 是过 WAF 的关键
        self.session = AsyncSession(
            impersonate="chrome120", 
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Upgrade-Insecure-Requests": "1",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            timeout=self.timeout,
            allow_redirects=True, # curl_cffi 自动处理重定向
            verify=False
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _clean_text(self, text: str) -> str:
        if not text: return ""
        text = text.replace('\xa0', ' ')
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _parse_html(self, html: str, final_url: str) -> Dict:
        """解析逻辑 (与之前一致)"""
        soup = BeautifulSoup(html, "html.parser")
        
        # 清理垃圾标签
        for tag in soup(["script", "style", "nav", "footer", "iframe", "svg", "noscript"]):
            tag.decompose()

        # 匹配配置
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

        # 提取数据
        if config:
            data["method"] = f"rule:{config.domain_keyword}"
            for sel in config.remove_selectors:
                for tag in soup.select(sel): tag.decompose()

            if soup.select_one(config.title_selector):
                data["title"] = self._clean_text(soup.select_one(config.title_selector).get_text())

            content_tags = soup.select(config.content_selector)
            data["content"] = "\n\n".join([self._clean_text(t.get_text()) for t in content_tags])

        # 兜底
        if not data["title"]:
            data["title"] = self._clean_text(soup.title.string if soup.title else "")
        if not data["content"]:
            data["method"] = "fallback"
            paras = [self._clean_text(p.get_text()) for p in soup.find_all("p") if len(p.get_text()) > 50]
            data["content"] = "\n\n".join(paras)

        return data

    async def scrape_url(self, url: str) -> Optional[Dict]:
        async with self.semaphore:
            try:
                # 随机延迟
                await asyncio.sleep(random.uniform(1.0, 3.0))
                logger.info(f"Fetching: {url}")
                
                # 发送请求 (模拟 Chrome)
                response = await self.session.get(url)
                
                # curl_cffi 的 response.url 已经是最终 URL
                final_url = str(response.url)

                if response.status_code != 200:
                    logger.error(f"Failed {response.status_code}: {final_url}")
                    # 如果是 403，通常代表还是被拦截，但 curl_cffi 成功率远高于 httpx
                    return None
                
                # 登录墙检查
                if "/login" in final_url.lower() and "/login" not in url.lower():
                    logger.warning(f"Hit Login Wall: {final_url}")
                    return None

                # 解析
                return self._parse_html(response.text, final_url)

            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
                return None

# --- 4. 运行入口 ---
async def main():
    # 更新了一组【有效的】URL，确保测试通过
    target_urls = [
        "https://www.medrxiv.org/lookup/external-ref?access_num=10.1111/bph.13059&link_type=DOI",
    ]

    results = []
    
    # 初始化抓取器
    async with BrowserScraper(concurrency=3) as scraper:
        tasks = [scraper.scrape_url(url) for url in target_urls]
        for f in asyncio.as_completed(tasks):
            res = await f
            if res:
                results.append(res)
                print(f"✅ [{res['domain']}] {res['title'][:30]}... (Len: {len(res['content'])})")

    # 保存
    with open("final_result.jsonl", "w", encoding="utf-8") as f:
        for item in results:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    
    logger.info(f"Done. Saved {len(results)} records.")

if __name__ == "__main__":
    # Windows 兼容性
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except:
        pass
    asyncio.run(main())