import os
import json
import logging
import time
import random
import asyncio
import datetime
import warnings
from typing import List, Literal, Optional, Dict
from urllib.parse import urlparse

# 抑制 langchain 在 Python 3.14 上的 Pydantic V1 警告
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="langchain_core._api.deprecation",
)

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field, ConfigDict
import re

# 导入缓存层
try:
    from services.discovery.cache import SearchCache, CacheMetrics
except ImportError:
    from cache import SearchCache, CacheMetrics

logger = logging.getLogger(__name__)


class DiscoveryService:
    def __init__(
        self,
        *,
        serper_api_key: Optional[str] = None,
        timeout: int = 15,
        max_retries: int = 3,
        backoff_base: float = 0.8,
        max_concurrency: int = 4,
        min_interval: float = 0.0,
        enable_cache: bool = True,
        cache_type: Optional[str] = None,
        cache_ttl: int = 86400,
    ):
        self.serper_api_key = serper_api_key or os.getenv("SERPER_API_KEY", "3286197ccfe6328c1fb98a15da9d2e4f4e5e3465")
        self.base_url = "https://google.serper.dev/search"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.max_concurrency = max_concurrency
        self.min_interval = min_interval
        self._last_request_ts = 0.0
        self._throttle_lock = asyncio.Lock()

        # 使用 httpx.AsyncClient 替代 requests.Session
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=max(self.max_concurrency * 2, 8),
                max_keepalive_connections=max(self.max_concurrency, 4),
            ),
        )
        
        # 初始化缓存层
        self.enable_cache = enable_cache
        self.cache = None
        self.metrics = CacheMetrics()
        if enable_cache:
            try:
                self.cache = SearchCache(
                    cache_type=cache_type,
                    ttl_seconds=cache_ttl,
                )
                logger.info("缓存层已启用: %s", self.cache.cache_type)
            except Exception as e:
                logger.warning("缓存初始化失败，将直接调用 API: %s", e)
                self.enable_cache = False
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出，关闭客户端"""
        await self.close()
    
    async def close(self):
        """关闭 httpx 客户端连接"""
        await self.client.aclose()

    async def find_discussion_urls(self, dork_result: "DorkResult", *, per_dork: int = 20) -> List["DiscoveryItem"]:
        """根据 DorkResult 批量搜索，返回去重后的结构化结果。

        - dork_result: EnterpriseDorkGenerator.run 返回的结果
        - per_dork: 每个 dork 请求的最大结果数
        """
        if not self.serper_api_key:
            logger.error("SERPER_API_KEY 未配置，无法执行搜索")
            return []

        if not dork_result or not dork_result.dorks:
            logger.warning("空的 DorkResult，跳过搜索")
            return []

        results_map: Dict[str, "DiscoveryItem"] = {}
        headers = {
            "X-API-KEY": self.serper_api_key,
            "Content-Type": "application/json",
        }

        # 使用 asyncio.gather 并发执行所有搜索任务
        tasks = [
            self._search_one_dork(dork=dork, per_dork=per_dork, headers=headers)
            for dork in dork_result.dorks
        ]
        
        # 使用 asyncio.Semaphore 控制并发数
        semaphore = asyncio.Semaphore(self.max_concurrency)
        
        async def bounded_task(task):
            async with semaphore:
                return await task
        
        # 并发执行所有任务
        results = await asyncio.gather(
            *[bounded_task(task) for task in tasks],
            return_exceptions=True
        )
        
        # 处理结果
        for i, result in enumerate(results):
            dork = dork_result.dorks[i]
            if isinstance(result, Exception):
                logger.warning("Dork 任务异常: %s | error=%s", dork, result)
                continue
            
            for item in result:
                key = self._normalize_url(item.url)
                if key and key not in results_map:
                    results_map[key] = item

        logger.info("汇总后有效链接: %d", len(results_map))
        
        # 输出缓存统计
        if self.enable_cache:
            stats = self.metrics.get_stats()
            logger.info(
                "缓存统计: 命中率=%.1f%% | API调用=%d次 | 节省成本=$%.4f",
                stats["hit_rate_percent"],
                stats["api_calls"],
                stats["cost_saved_usd"],
            )
        
        return list(results_map.values())

    async def _throttle(self) -> None:
        # 简单的全局节流，确保请求间隔不低于 min_interval
        if self.min_interval <= 0:
            return
        async with self._throttle_lock:
            now = time.time()
            elapsed = now - self._last_request_ts
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_request_ts = time.time()

    async def _search_one_dork(self, *, dork: str, per_dork: int, headers: dict) -> List["DiscoveryItem"]:
        # 1. 尝试从缓存获取
        if self.enable_cache and self.cache:
            cached_result = self.cache.get(dork)
            if cached_result is not None:
                self.metrics.record_hit()
                items = self._extract_valid_items(cached_result, dork=dork)
                logger.info("[缓存命中] Dork: %s | hits=%d", dork[:60], len(items))
                return items
            else:
                self.metrics.record_miss()
        
        # 2. 缓存未命中，调用 Serper API
        payload_data = {"q": dork, "num": per_dork}

        for attempt in range(1, self.max_retries + 1):
            try:
                # 节流控制
                await self._throttle()
                
                # 使用 httpx 异步请求
                resp = await self.client.post(
                    self.base_url,
                    headers=headers,
                    json=payload_data,
                )
                resp.raise_for_status()
                results = resp.json()
                
                # 3. 写入缓存
                if self.enable_cache and self.cache:
                    self.cache.set(dork, results)
                
                # 调试输出：打印原始 Serper 响应
                logger.debug("=== Serper 原始响应 for dork: %s ===", dork[:80])
                organic = results.get("organic", [])
                for idx, item in enumerate(organic[:3]):  # 只打印前3条
                    logger.debug("  [%d] link=%s", idx+1, item.get("link"))
                    logger.debug("      title=%s", item.get("title", "")[:100])
                
                items = self._extract_valid_items(results, dork=dork)
                logger.info("[API调用] Dork 搜索完成: hits=%d url=%s", len(items), dork[:60])
                return items
            except Exception as e:
                if attempt >= self.max_retries:
                    logger.warning("Dork 搜索失败(已达上限): %s | error=%s", dork, e)
                    break
                sleep_s = self.backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.2)
                logger.warning("Dork 搜索失败，第 %d 次重试 %.2fs 后: %s", attempt, sleep_s, e)
                await asyncio.sleep(sleep_s)
        return []

    def _normalize_url(self, link: Optional[str]) -> Optional[str]:
        if not link:
            return None
        try:
            parsed = urlparse(link)
            cleaned_path = parsed.path.rstrip("/") or "/"
            normalized = parsed._replace(path=cleaned_path, fragment="").geturl()
            return normalized
        except Exception:
            return None

    def _extract_valid_items(self, results: dict, *, dork: str) -> List["DiscoveryItem"]:
        """从 serper 返回结果中提取经过平台验证的结构化结果"""
        items: List[DiscoveryItem] = []
        organic = results.get("organic", []) if isinstance(results, dict) else []
        
        logger.debug("处理 dork 结果: %s, 原始条目数: %d", dork[:60], len(organic))
        
        for idx, item in enumerate(organic):
            link = item.get("link")
            if not self._validate_link(link):
                logger.debug("  [%d] 过滤: %s (验证失败)", idx+1, link)
                continue
            normalized = self._normalize_url(link)
            if not normalized:
                logger.debug("  [%d] 过滤: %s (标准化失败)", idx+1, link)
                continue
            try:
                parsed = urlparse(normalized)
                title = item.get("title")
                rank = item.get("position") or idx + 1
                items.append(
                    DiscoveryItem(
                        url=normalized,
                        domain=parsed.netloc.lower(),
                        dork=dork,
                        rank=int(rank) if isinstance(rank, (int, float)) else idx + 1,
                        title=title,
                    )
                )
                logger.debug("  [%d] ✓ 保留: %s", idx+1, normalized)
            except Exception as e:
                logger.debug("  [%d] 过滤: %s (解析异常: %s)", idx+1, link, e)
                continue
        return items

    def _validate_link(self, link: Optional[str]) -> bool:
        """简单过滤，防止抓到个人主页或无效页面"""
        if not link:
            return False

        try:
            parsed = urlparse(link)
            host = parsed.netloc.lower()
        except Exception:
            return False

        if "reddit.com" in host:
            # 允许板块内的帖子和讨论，排除个人主页、系统页面
            path = parsed.path
            if path.startswith("/r/"):
                # 排除板块的 wiki、about、search 等系统页面
                if any(x in path for x in ["/about", "/wiki", "/search"]):
                    return False
                # 允许板块主页、帖子页面（包括 /comments/）
                return True
            return False
        if "tiktok.com" in host:
            return "/video/" in parsed.path
        if "youtube.com" in host or "youtu.be" in host:
            return "watch?v=" in link or "/shorts/" in parsed.path

        # 其他站点放行，后续可扩展
        return True
    
    
# 定义支持的领域类型
DomainType = Literal["B2C_CONSUMER", "B2B_ENTERPRISE", "TECHNICAL_DEV", "ACADEMIC_MEDICAL"]

class SearchStrategy(BaseModel):
    """第一步：分析话题属性"""
    domain: DomainType = Field(..., description="The classification of the research topic.")
    primary_keywords_en: List[str] = Field(..., description="Top 3 translated English keywords.")
    target_platforms: List[str] = Field(..., description="List of domains to search (e.g., reddit.com, g2.com).")
    time_window_start: str = Field(..., description="YYYY-MM-DD format. Usually 1-2 years back.")

class DorkResult(BaseModel):
    """第二步：生成的具体指令
    
    Should contain 10+ diversified dorks covering multiple platforms and keyword angles,
    not concentrated on a single source path.
    """
    dorks: List[str]


class DiscoveryItem(BaseModel):
    url: str
    domain: str
    dork: str
    rank: int
    title: Optional[str] = None

class EnterpriseDorkGenerator:
    def __init__(self):
        base_url = os.getenv("BASE_URL", "https://chrisapivip.com/v1")
        api_key = os.getenv("API_KEY", "sk-wvbHvCfLHCvCf0kHEB8xTOInLVfZtDe4rNB0FiHQxgbQ0OhY")   
    
        self.llm = ChatOpenAI(model="gpt-4o",base_url=base_url,api_key=api_key, temperature=0)
    
    def _clean_json_from_markdown(self, text: str) -> str:
        """清理LLM输出中的Markdown包裹和多余的格式
        
        处理以下情况：
        - ```json ... ```
        - ```
          ...
          ```
        - 多余的换行和空格
        """
        # 移除 Markdown 代码块标记
        text = re.sub(r'^```(?:json)?\s*\n', '', text.strip(), flags=re.MULTILINE)
        text = re.sub(r'\n```\s*$', '', text.strip(), flags=re.MULTILINE)
        
        # 移除可能的注释（// 或 #）
        text = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)
        text = re.sub(r'#.*?$', '', text, flags=re.MULTILINE)
        
        return text.strip()
    
    def _safe_parse(self, parser: PydanticOutputParser, text: str, model_name: str = "Strategy") -> any:
        """安全地解析LLM输出，带有自动修复功能
        
        Args:
            parser: PydanticOutputParser实例
            text: LLM的原始输出文本
            model_name: 模型名称（用于日志）
            
        Returns:
            解析后的Pydantic对象
            
        Raises:
            Exception: 如果所有解析尝试都失败
        """
        # 清理Markdown包裹
        cleaned_text = self._clean_json_from_markdown(text)
        
        # 尝试1: 直接解析清理后的文本
        try:
            return parser.parse(cleaned_text)
        except Exception as e1:
            logger.warning(f"{model_name}解析尝试1失败: {str(e1)[:100]}")
            
            # 尝试2: 如果清理后的文本解析失败，尝试重新用LLM修复
            try:
                # 构造修复提示词
                fix_prompt = f"""
The following JSON output has parsing errors. Please fix it and return ONLY valid JSON:

{cleaned_text}

Requirements:
1. Fix any JSON syntax errors (missing quotes, commas, brackets)
2. Ensure all strings are properly quoted
3. Return ONLY the corrected JSON, no explanations
4. Do NOT wrap in markdown code blocks
"""
                logger.info(f"{model_name}: 尝试用LLM修复JSON...")
                fix_response = self.llm.invoke(fix_prompt)
                fixed_text = self._clean_json_from_markdown(fix_response.content)
                return parser.parse(fixed_text)
            except Exception as e2:
                logger.warning(f"{model_name}解析尝试2失败: {str(e2)[:100]}")
                
                # 尝试3: 尝试手动修复常见的JSON错误
                try:
                    # 修复常见的单引号问题
                    fixed = cleaned_text.replace("'", '"')
                    # 修复尾随逗号
                    fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)
                    return parser.parse(fixed)
                except Exception as e3:
                    logger.error(f"{model_name}解析尝试3失败: {str(e3)[:100]}")
                    logger.error(f"原始文本前200字符: {text[:200]}")
                    logger.error(f"清理后文本前200字符: {cleaned_text[:200]}")
                    raise Exception(f"{model_name}解析失败，已尝试3种方法: {str(e3)}")
    
    def _get_domain_sources(self, domain: str, primary_keywords: List[str]) -> Dict[str, List[str]]:
        """为不同领域返回多元化的源列表，避免局限于单一路径
        
        Returns:
            {source_type: [site1, site2, ...]}
        """
        sources = {
            "B2C_CONSUMER": {
                "social_media": [
                    "site:reddit.com",           # 不限定特定板块，而是全Reddit
                    "site:tiktok.com",
                    "site:instagram.com",
                    "site:youtube.com",
                ],
                "commerce": [
                    "site:amazon.com/reviews",
                    "site:trustpilot.com",
                    "site:g2.com",
                ],
                "media": [
                    "site:voguebusiness.com",
                    "site:vogue.com",
                    "site:forbes.com",
                    "site:businessinsider.com",
                    "site:fastcompany.com",
                ],
                "blogs": [
                    "site:medium.com",
                    "site:substack.com",
                    "site:cosmoproof.com",
                    "site:beautyindependent.com",
                ]
            },
            "B2B_ENTERPRISE": {
                "industry_news": [
                    "site:supplychaindive.com",
                    "site:bloomberg.com",
                    "site:reuters.com",
                    "site:ft.com",
                    "site:wsj.com",
                ],
                "professional": [
                    "site:linkedin.com",
                    "site:g2.com",
                    "site:capterra.com",
                ],
                "reports": [
                    "filetype:pdf site:.edu",
                    "filetype:pdf site:.gov",
                    "filetype:pptx",
                ],
                "forums": [
                    "site:reddit.com/r/business",
                    "site:reddit.com/r/startups",
                    "site:ycombinator.com",
                ]
            },
            "TECHNICAL_DEV": {
                "code": [
                    "site:github.com",
                    "site:gitlab.com",
                ],
                "qa": [
                    "site:stackoverflow.com",
                    "site:superuser.com",
                    "site:unix.stackexchange.com",
                ],
                "docs_blogs": [
                    "site:dev.to",
                    "site:medium.com",
                    "site:hashnode.com",
                    "site:substack.com",
                ],
                "news": [
                    "site:hackernews.com",
                    "site:techcrunch.com",
                    "site:arstechnica.com",
                ]
            },
            "ACADEMIC_MEDICAL": {
                "journals": [
                    "site:pubmed.ncbi.nlm.nih.gov",
                    "site:scholar.google.com",
                    "site:researchgate.net",
                ],
                "preprints": [
                    "site:arxiv.org",
                    "site:biorxiv.org",
                    "site:medrxiv.org",
                ],
                "institutions": [
                    "site:.edu",
                    "site:nih.gov",
                    "site:cdc.gov",
                ],
                "books": [
                    "filetype:pdf",
                ]
            }
        }
        return sources.get(domain, {})
    
    def run(self, topic: str) -> "DorkResult":
        # 1. 获取当前时间，用于动态计算“最近一年”
        today = datetime.date.today()
        one_year_ago = today - datetime.timedelta(days=365)
        
        # --- Phase 1: 战略规划 (Strategy) ---

        # ==============================================================================
        # 1. 定义 Few-Shot Examples (这是让模型变聪明的关键)
        # ==============================================================================
        FEW_SHOT_EXAMPLES = """
        <Example 1: The "False Friend" Trap (Animal vs. Code)>
        Input: "Python Pandas 内存溢出解决方案"
        Thinking:
        - Subject: "Pandas". Is it the animal? No, context is "memory leak" (内存溢出).
        - Domain: This is a technical programming issue.
        - Audience: Developers, Data Scientists.
        - Invalid Platforms: TikTok (too shallow), LinkedIn (too corporate).
        - Valid Platforms: StackOverflow, GitHub Issues, Tech Blogs.
        Decision:
        - Domain: TECHNICAL_DEV
        - Keywords_EN: ["python pandas memory leak fix", "dataframe optimization", "out of memory error"]
        - Platforms: ["site:stackoverflow.com", "site:github.com", "site:medium.com"]
        </Example 1>

        <Example 2: The B2C/B2B Split (Product vs. Supply Chain)>
        Input: "Tesla Cybertruck 电池供应链分析"
        Thinking:
        - Subject: Tesla Cybertruck. Usually B2C.
        - Intent: "Supply Chain Analysis" (供应链分析). This is a business/industrial topic, not a driver review.
        - Audience: Investors, Competitors, Manufacturing Experts.
        - Invalid Platforms: Reddit r/TeslaMotors (fanboys), YouTube (reviews).
        - Valid Platforms: Industry Reports, Reuters, Supply Chain Dive.
        Decision:
        - Domain: B2B_ENTERPRISE
        - Keywords_EN: ["Tesla Cybertruck battery supply chain", "4680 cell suppliers", "lithium sourcing"]
        - Platforms: ["filetype:pdf", "site:bloomberg.com", "site:reuters.com", "site:supplychaindive.com"]
        </Example 2>

        <Example 3: The Consumer Trend (Viral/Sentiment) - MULTI-SOURCE>
        Input: "2026 北美 纯净美妆 营销趋势"
        Thinking:
        - Subject: Clean Beauty (Skincare).
        - Intent: Marketing Trends. In FMCG, trends come from consumer sentiment and viral content.
        - Audience: Marketers looking for what consumers want.
        - Source of Truth: NOT just reddit.com/r/SkincareAddiction. Multiple sources needed:
          * Reddit (general discussions, not just one subreddit)
          * TikTok (viral trends)
          * Beauty industry news (marketing angle)
          * E-commerce reviews (what consumers actually buy)
          * Influencer/media discussions
        - Keyword Angles: Viral trends, consumer pain points, forecast, marketing strategies
        Decision:
        - Domain: B2C_CONSUMER
        - Keywords_EN: ["clean beauty viral trends 2025", "clean beauty controversy", "skincare marketing forecast"]
        - Platforms: DO NOT limit to one subreddit! Cover:
          * site:reddit.com (beauty, skincare, general - multiple communities)
          * site:tiktok.com
          * site:voguebusiness.com
          * site:trustpilot.com (reviews)
          * site:medium.com (influencer insights)
        Generated Dorks (example - should generate 8-10 with this pattern):
          1. site:reddit.com clean beauty (trends OR viral OR 2026) -coupon -buy
          2. site:tiktok.com skincare (overrated OR controversy OR future) after:2024-01-01
          3. site:voguebusiness.com clean beauty marketing 2026
          4. site:trustpilot.com (skincare OR cosmetics) (review OR complaint) after:2024-01-01
          5. site:medium.com skincare industry trends forecast
        </Example 3>

        <Example 4: The Academic/Medical Niche>
        Input: "GLP-1 减肥药的长期副作用机制"
        Thinking:
        - Subject: GLP-1 (Ozempic/Wegovy).
        - Intent: "Mechanism of side effects" (副作用机制). This is scientific/medical, not just user complaints.
        - Source of Truth: Clinical trials, Medical Journals.
        - Invalid Platforms: Instagram (anecdotal), LinkedIn.
        Decision:
        - Domain: ACADEMIC_MEDICAL
        - Keywords_EN: ["GLP-1 long term side effects mechanism", "semaglutide clinical trial results", "safety profile"]
        - Platforms: ["site:nih.gov", "site:pubmed.ncbi.nlm.nih.gov", "site:nejm.org", "filetype:pdf"]
        </Example 4>
        """

        # ==============================================================================
        # 2. 组装 System Prompt
        # ==============================================================================
        STRATEGY_SYSTEM_PROMPT = f"""
        # Role
        You are the **Head of Research Strategy** at an elite intelligence firm.
        Your goal is to route the user's research topic to the strictly correct **Internet Domain** and translate it into high-precision English search keywords.

        # The 4 Domains
        1. **B2C_CONSUMER:** Physical products, games, entertainment, fashion.
        - *Source of Truth:* Reddit, TikTok, YouTube, Instagram.
        - *Keywords:* "Review", "Fail", "Viral", "Worth it?".
        
        2. **B2B_ENTERPRISE:** SaaS, Logistics, Manufacturing, Finance, Corporate Strategy.
        - *Source of Truth:* LinkedIn, G2, Gartner, Whitepapers (PDF), Industry News.
        - *Keywords:* "ROI", "Case Study", "Implementation", "Market Share".
        
        3. **TECHNICAL_DEV:** Coding, APIs, Cloud Infra, Hardware Engineering.
        - *Source of Truth:* GitHub, StackOverflow, HackerNews, Dev.to.
        - *Keywords:* "Error", "Docs", "Benchmark", "Latency".
        
        4. **ACADEMIC_MEDICAL:** Biology, Physics, Deep Tech, Clinical Studies.
        - *Source of Truth:* Google Scholar, PubMed, Arxiv, University Sites (.edu).
        - *Keywords:* "Study", "Analysis", "Mechanism", "Data".

        # Reference Examples (Study these carefully!)
        {FEW_SHOT_EXAMPLES}

        # Current Task
        Current Date: {{current_date}}
        User Input: "{{topic}}"

        # Output Instructions
        1. **Analyze Intent:** Don't just look at the noun (e.g., "Tesla"). Look at the verb/modifier (e.g., "Supply Chain").
        2. **Translate:** Convert the core concept to Professional English.
        3. **Select Platforms:** Choose 3-5 specific domains that host high-density information for this topic.
        4. **Output Format:** {{format_instructions}}
        """

        parser = PydanticOutputParser(pydantic_object=SearchStrategy)
        strategy_prompt = STRATEGY_SYSTEM_PROMPT.format(
            current_date=today.isoformat(),
            topic=topic,
            format_instructions=parser.get_format_instructions()
        )
        
        response = self.llm.invoke(strategy_prompt)
        # 使用安全解析方法
        strategy = self._safe_parse(parser, response.content, model_name="SearchStrategy")

        # --- Phase 2: 战术执行 (Dorks Generation) ---
        domain_sources = self._get_domain_sources(strategy.domain, strategy.primary_keywords_en)
        
        # 按照来源类型组织平台列表
        platforms_by_type = []
        for source_type, sites in domain_sources.items():
            platforms_by_type.extend(sites)
        
        dork_parser = PydanticOutputParser(pydantic_object=DorkResult)
        dork_prompt = f"""
        Role: Google Search Expert specializing in multi-source coverage.
        Context: Researching "{topic}" in domain "{strategy.domain}".
        Keywords: {strategy.primary_keywords_en}
        Time Start: {strategy.time_window_start}
        
        Task: Generate 10 Google Dorks with DIVERSIFIED SOURCES.
        
        **CRITICAL RULE - Avoid Over-Restriction:**
        ⚠️  DO NOT limit to narrow paths like `site:reddit.com/r/SkincareAddiction`
        ✅ DO use broad platform access like `site:reddit.com`, `site:tiktok.com`, etc.
        ✅ DO cover multiple source types to get comprehensive information
        ✅ DO vary the keyword angles (pain points, trends, forecasts, controversies)
        
        **LENGTH CONSTRAINT:**
        ⚠️ IMPORTANT: Each dork MUST be ≤120 characters total (including spaces and operators).
        ✅ Keep it concise: site: + keywords + operators + time filter
        ✅ Example good length: "site:reddit.com skincare (trends OR viral) -coupon after:2024-01-01" (78 chars)
        ❌ Example too long: Over 120 chars will be truncated by search engine!
        
        Available Diversified Sources (pick from these):
        {json.dumps(domain_sources, indent=2)}
        
        Strategy:
        1. **Multi-Source Coverage:** Generate dorks across DIFFERENT PLATFORMS (not all from one site).
           - Example pattern: 2-3 dorks from reddit.com, 2-3 from news sites, 2 from commerce, etc.
        2. **Keyword Angles:** Vary the keywords to capture:
           - Consumer sentiment (viral, controversy, overrated, worth it)
           - Trend forecasts (2026, upcoming, next big thing, future of)
           - Pain points (issue, fail, problem, regret, side effect)
           - Insider insights (behind the scenes, strategy, marketing)
        3. **Syntax Rules:**
           - Use `site:` for platform targeting
           - Use `after:{strategy.time_window_start}` for time filtering
           - Only add `filetype:pdf` for academic/enterprise sources (.edu, .gov, reports)
           - DO NOT use `filetype:pdf` for social media
        4. **Anti-Noise:**
           - B2B/Tech: `-jobs -hiring -courses` (max 3 filters)
           - B2C: `-coupon -code -discount` (max 3 filters)
           - Social: Don't add noise filters; let the platform's algorithm work
        5. **Avoid Long Exact Matches:**
           - Bad: `site:reddit.com "very specific long phrase about skincare marketing trends"`
           - Good: `site:reddit.com skincare (trends OR viral OR forecast) after:2024-01-01`
        
        Output exactly 10 dorks, ensuring they span different sources and perspectives.
        VERIFY EACH DORK LENGTH ≤120 CHARACTERS BEFORE OUTPUTTING!
        
        {dork_parser.get_format_instructions()}
        """
        
        dork_response = self.llm.invoke(dork_prompt)
        # 使用安全解析方法
        result = self._safe_parse(dork_parser, dork_response.content, model_name="DorkResult")
        return result

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    async def main():
        generator = EnterpriseDorkGenerator()
        
        # 使用异步上下文管理器
        async with DiscoveryService() as discovery:
            test_topic = "北美市场2026年护肤品营销趋势分析"
            dork_result = generator.run(test_topic)
            print(f"\n生成的 Dorks:")
            for i, dork in enumerate(dork_result.dorks, 1):
                print(f"  {i}. {dork}")
            
            results = await discovery.find_discussion_urls(dork_result)
            print(f"\n示例: {test_topic}")
            print(f"返回 {len(results)} 条结果（去重后）")
            for item in results:
                print(" -", item.url)
    
    # 运行异步主函数
    asyncio.run(main())