from functools import lru_cache
import logging
import time
import random
import asyncio
from typing import List, Optional, Dict
from urllib.parse import urlparse
from config.config import config
import httpx
from services.search.dorks import DorkResult
from pydantic import BaseModel
from services.search.domains import DOMAINS

# 导入缓存层
try:
    from services.search.cache import SearchCache, CacheMetrics
except ImportError:
    from cache import SearchCache, CacheMetrics

logger = logging.getLogger(__name__)

@lru_cache()
def get_search_service() -> "SearchService":
    """全局唯一SearchService实例，推荐复用"""
    return SearchService()

class DiscoveryItem(BaseModel):
    url: str
    domain: str
    dork: str
    rank: int
    title: Optional[str] = None

class SearchService:
    def __init__(
        self,
        timeout: int = 15,
        max_retries: int = 3,
        backoff_base: float = 0.8,
        max_concurrency: int = 4,
        min_interval: float = 0.0,
        enable_cache: bool = True,
        cache_type: Optional[str] = None,
        cache_ttl: int = 86400,
    ):
        self.serper_api_key = config.SERPER_API_KEY
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

    async def find_discussion_urls(self, dork_result: List[str], *, per_dork: int = 20) -> List["DiscoveryItem"]:
        """根据 DorkResult 批量搜索，返回去重后的结构化结果。

        - dork_result: EnterpriseDorkGenerator.run 返回的结果
        - per_dork: 每个 dork 请求的最大结果数
        """
        if not self.serper_api_key:
            logger.error("SERPER_API_KEY 未配置，无法执行搜索")
            return []

        if not dork_result:
            logger.warning("空的 DorkResult，跳过搜索")
            return []

        results_map: Dict[str, "DiscoveryItem"] = {}
        headers = {
            "X-API-KEY": self.serper_api_key,
            "Content-Type": "application/json",
        }

        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def search_with_semaphore(dork):
            async with semaphore:
                return await self._search_one_dork(dork=dork, per_dork=per_dork, headers=headers)

        # 并发执行所有任务，信号量控制在任务函数内部
        tasks = [search_with_semaphore(dork) for dork in dork_result]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 处理结果
        for i, result in enumerate(results):
            dork = dork_result[i]
            if isinstance(result, Exception):
                logger.warning("Dork 任务异常: %s | error=%s", dork, result)
                continue
            for item in result:
                key = self._normalize_url(item.url)
                if key and key not in results_map:
                    results_map[key] = item

        logger.info("汇总后有效链接: %d", len(results_map))

        all_items = list(results_map.values())

        # 输出缓存统计
        if self.enable_cache:
            stats = self.metrics.get_stats()
            logger.info(
                "缓存统计: 命中率=%.1f%% | API调用=%d次 | 节省成本=$%.4f",
                stats["hit_rate_percent"],
                stats["api_calls"],
                stats["cost_saved_usd"],
            )

        return all_items

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

    @staticmethod
    @lru_cache()
    def _build_allowed_domain_tokens() -> List[str]:
        """从 services.search.domains.DOM AINS 中解析出允许的 domain token 列表。

        解析规则：查找字符串中 'site:' 后的部分，去除路径，仅保留主机或后缀（例如 '.edu' 会被保留）。
        返回的 token 可用于后续的后缀或精确匹配检测。
        """
        tokens: List[str] = []
        for entry in DOMAINS:
            site_val = entry.get("site", "") if isinstance(entry, dict) else ""
            if not site_val or "site:" not in site_val:
                # 只处理明确包含 site: 的条目，忽略 filetype: 或其他非 site 条目
                continue
            part = site_val.split("site:", 1)[1].strip()
            # 如果包含空格或其他 operators, 只取第一个 token
            part = part.split()[0]
            # 取第一个 path segment（域名部分）
            domain_candidate = part.split("/")[0]
            if not domain_candidate:
                continue
            domain_candidate = domain_candidate.lower()
            # 基本合法性检查：必须包含至少一个点或以 . 开头（如 .edu）
            if '.' not in domain_candidate and not domain_candidate.startswith('.'):
                continue
            tokens.append(domain_candidate)
        # 去重并返回
        tokens = list(dict.fromkeys(tokens))
        logger.debug("Allowed domain tokens built: %s", tokens)
        return tokens

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

        # 先检查域名是否在 DOMAINS 白名单中
        allowed_tokens = self._build_allowed_domain_tokens()

        def host_matches_token(h: str, token: str) -> bool:
            if token.startswith('.'):
                return h.endswith(token)
            if h == token:
                return True
            return h.endswith('.' + token)

        domain_allowed = any(host_matches_token(host, tok) for tok in allowed_tokens)
        if not domain_allowed:
            logger.debug("链接域名未在白名单，拒绝: %s", host)
            return False

        # 域名被允许后，再应用已有的站点级路径过滤规则
        # Reddit: 只允许 /r/ 下的讨论和帖子，排除 wiki/about/search
        if "reddit.com" in host:
            path = parsed.path
            if not path.startswith("/r/"):
                logger.debug("reddit 链接非 /r/ 下，拒绝: %s", link)
                return False
            if any(x in path for x in ["/about", "/wiki", "/search"]):
                logger.debug("reddit 链接为系统页面，拒绝: %s", link)
                return False
            return True

        # TikTok: 仅允许含 /video/ 的内容页
        if "tiktok.com" in host:
            ok = "/video/" in parsed.path
            if not ok:
                logger.debug("tiktok 非视频页，拒绝: %s", link)
            return ok

        # YouTube: 仅允许 watch 或 shorts
        if "youtube.com" in host or "youtu.be" in host:
            ok = ("watch?v=" in link) or ("/shorts/" in parsed.path)
            if not ok:
                logger.debug("youtube 非 watch/shorts，拒绝: %s", link)
            return ok

        # 其他已在白名单域名，允许
        return True

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

        # 额外过滤：仅允许在 DOMAINS 列表中出现的域名（或其子域）
        allowed_tokens = self._build_allowed_domain_tokens()
        def host_matches_token(h: str, token: str) -> bool:
            # token 可能为 .edu 或 example.com
            if token.startswith('.'):
                return h.endswith(token)
            if h == token:
                return True
            return h.endswith('.' + token)

        for tok in allowed_tokens:
            if host_matches_token(host, tok):
                return True

        logger.debug("  链接域名不在 DOMAINS 白名单中: %s | allowed_tokens=%s", host, allowed_tokens)
        return False
