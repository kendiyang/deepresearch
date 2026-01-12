    
from pydantic import BaseModel, Field
from typing import List, Literal, Optional, Dict
import datetime
import json
from langchain_openai import ChatOpenAI
import logging
from config.config import config
from utils.struct_parser import safe_parse
from langchain_core.output_parsers import PydanticOutputParser
from functools import lru_cache


logger = logging.getLogger(__name__)


@lru_cache()
def get_dork_generator():
    return EnterpriseDorkGenerator()

# 定义支持的领域类型
DomainType = Literal["B2C_CONSUMER", "B2B_ENTERPRISE", "TECHNICAL_DEV", "ACADEMIC_MEDICAL"]

class SearchStrategy(BaseModel):
    """第一步：分析话题属性"""
    domain: DomainType = Field(..., description="The classification of the research topic.")
    primary_keywords_en: List[str] = Field(..., description="3 English search phrases covering different research angles (e.g., pain points, trends, solutions) to guide multi-source dork generation.")
    target_platforms: List[str] = Field(..., description="List of domains to search (e.g., reddit.com, g2.com).")
    time_window_start: str = Field(..., description="YYYY-MM-DD format. Usually one years back.")

class DorkResult(BaseModel):
    """第二步：生成的具体指令
    
    Should contain 10+ diversified dorks covering multiple platforms and keyword angles,
    not concentrated on a single source path.
    """
    dorks: List[str]
    
    def model_post_init(self,__context) -> None:
        """初始化后验证，过滤无效 dork"""
        import re
        original_count = len(self.dorks)
        
        def is_valid_dork(dork: str) -> bool:
            if not dork or len(dork) < 15:  # 太短的 dork 必然无效
                return False
            # 移除 site:, after:, filetype:, -过滤符后，必须还有关键词
            remaining = re.sub(r'site:[^\s]+', '', dork)
            remaining = re.sub(r'after:[^\s]+', '', remaining)
            remaining = re.sub(r'filetype:[^\s]+', '', remaining)
            remaining = re.sub(r'-\w+', '', remaining).strip()
            if len(remaining) < 5:  # 关键词部分至少 5 个字符
                logger.warning(f"过滤无效dork（缺少关键词）: {dork}")
                return False
            return True
        
        self.dorks = [d for d in self.dorks if is_valid_dork(d)]
        filtered = original_count - len(self.dorks)
        if filtered > 0:
            logger.warning(f"过滤掉 {filtered} 个无效dork，剩余 {len(self.dorks)} 个")


class EnterpriseDorkGenerator:
    def __init__(self):
        base_url = config.OPENAI_BASE_URL
        api_key = config.OPENAI_API_KEY 
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未配置，请在 .env 或环境变量中设置")
    
        self.llm = ChatOpenAI(model="gpt-4o", base_url=base_url, api_key=api_key, temperature=0)

        if self.llm is None:
            raise ValueError("EnterpriseDorkGenerator实例未正确初始化")
    
    def _get_domain_sources(self, domain: str, primary_keywords: List[str]) -> Dict[str, List[str]]:
        """为不同领域返回多元化的源列表，避免局限于单一路径
        
        Returns:
            {source_type: [site1, site2, ...]}
        """
        '''
        sources = {
            "B2C_CONSUMER": {
                "social_media": [
                    "site:reddit.com",           # 不限定特定板块，而是全Reddit
                    "site:tiktok.com",
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
                "trends": [ 
                    "site:google.com/trends",
                    "site:trendhunter.com",
                    "site:explodingtopics.com",
                    "site:trends.pinterest.com",
                    "site:beautyindependent.com",
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
                "trends": [ 
                    "site:google.com/trends",
                    "site:trendhunter.com",
                    "site:explodingtopics.com",
                    "site:trends.pinterest.com",
                    "site:beautyindependent.com",
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
        '''

        return {"social_media": [
                    "site:reddit.com",           # 不限定特定板块，而是全Reddit
                ]}
    
    async def run(self, topic: str) -> "DorkResult":
        # 1. 获取当前时间，用于动态计算“最近一年”
        today = datetime.date.today()
        one_year_ago = today - datetime.timedelta(days=365)
        
        # --- Phase 1: 战略规划 (Strategy) ---

        # ==============================================================================
        # 1. 定义 Few-Shot Examples (这是让模型变聪明的关键)
        # ==============================================================================
        one_year_ago_str = one_year_ago.isoformat()
        FEW_SHOT_EXAMPLES = f"""
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
        Input: "2025 北美 纯净美妆 营销趋势"
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
          * Trends: recent trends (google.com/trends,trends.pinterest.com)
        Generated Dorks (example - should generate 10-25 with this pattern):
          1. site:reddit.com clean beauty (trends OR viral OR 2025) -coupon -buy
          2. site:tiktok.com skincare (overrated OR controversy OR future) after:{one_year_ago_str}
          3. site:voguebusiness.com clean beauty marketing 2025
          4. site:trustpilot.com (skincare OR cosmetics) (review OR complaint) after:{one_year_ago_str}
          5. site:medium.com skincare industry trends forecast
          6. site:google.com/trends clean beauty 2025
          7. site:trends.pinterest.com skincare trends 2025
          8. site:explodingtopics.com clean beauty viral products
          9. site:trendhunter.com clean beauty marketing strategies
         10. site:beautyindependent.com clean beauty industry analysis
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

        ⚠️ <CRITICAL: Common Mistakes to AVOID>
        
        ❌ WRONG OUTPUT - Missing Keywords:
        Input: "护肤品趋势"
        Bad Dorks (DO NOT generate these):
          1. site:instagram.com                           ← NO KEYWORDS! Useless query
          2. site:tiktok.com                              ← NO KEYWORDS! Will return irrelevant content
          3. site:reddit.com after:{one_year_ago_str}     ← NO KEYWORDS! Too broad
        
        ✅ CORRECT OUTPUT - With Actual Keywords:
        Input: "护肤品趋势"
        Good Dorks (generate like this):
          1. site:instagram.com skincare trends 2026 -ad
          2. site:tiktok.com beauty viral products after:{one_year_ago_str}
          3. site:reddit.com skincare (trending OR popular OR upcoming) -coupon
        
        💡 Why This Matters:
        - "site:instagram.com" alone returns 1 billion+ irrelevant posts
        - "site:instagram.com skincare trends 2026" narrows to ~10K relevant posts
        - EVERY dork MUST have: site: + meaningful keywords (2-3 words minimum)
        
        Remember: The "site:" operator selects WHERE to search.
                  The keywords select WHAT to search for.
                  You need BOTH to generate useful results!
        </CRITICAL>
        """

        # ==============================================================================
        # 2. 组装 System Prompt
        # ==============================================================================
        STRATEGY_SYSTEM_PROMPT = f"""
        # Role
        You are the **Head of Research Strategy** at an elite intelligence firm.
        Your goal is to route the user's research topic to the strictly correct **Internet Domain** and translate it into high-precision English search keywords.

        # The 4 Domains

        ## 1. **B2C_CONSUMER** - Consumer Products & Lifestyle
        **Scope:** Physical products, fashion, beauty, food, entertainment, games, trending lifestyle topics.
        **Information Sources:**
        - **Social Media** (viral sentiment, peer reviews): Reddit (all communities), TikTok, YouTube
        - **Commerce Reviews** (purchase intent signals): Amazon reviews, Trustpilot, G2 user reviews
        - **Lifestyle Media** (editorial trends): Vogue, Forbes, Business Insider, Fast Company, Vogue Business
        - **Trend Databases** (search volume, forecast): Google Trends, Trend Hunter, Exploding Topics, Pinterest Trends
        - **Influencer Blogs** (thought leadership): Medium, Substack, independent beauty/lifestyle blogs
        **Key Indicators:** Review scores, viral mentions, trend velocity, sentiment polarity, influencer recommendations
        **Keywords Pattern:** "Review", "Viral", "Overrated", "Worth it?", "Upcoming trends", "Controversy", "User feedback"

        ## 2. **B2B_ENTERPRISE** - Business, SaaS, Manufacturing, Supply Chain
        **Scope:** B2B SaaS solutions, Enterprise tools, Supply Chain, Manufacturing, Corporate Finance, Business Strategy.
        **Information Sources:**
        - **Industry News** (market intelligence): Supply Chain Dive, Bloomberg, Reuters, Financial Times, Wall Street Journal
        - **Professional Networks** (peer evaluation): LinkedIn, G2 enterprise reviews, Capterra software comparisons
        - **Formal Reports** (academic/government analysis): PDF reports from .edu/.gov sites, PowerPoint case studies, whitepapers
        - **Trend Analysis** (market forecasts): Google Trends for B2B, Trend Hunter, Exploding Topics, Startup trends
        - **Industry Forums** (expert discussion): Reddit r/business, Reddit r/startups, Y Combinator discussions
        **Key Indicators:** ROI metrics, implementation case studies, market share, customer acquisition cost, competitive positioning
        **Keywords Pattern:** "ROI", "Case Study", "Implementation", "Market Share", "Enterprise solution", "Vendor comparison"

        ## 3. **TECHNICAL_DEV** - Software Development, Engineering, Infrastructure
        **Scope:** Programming languages, APIs, Cloud Infrastructure, DevOps, Hardware Engineering, Data Science.
        **Information Sources:**
        - **Code Repositories** (working solutions): GitHub, GitLab, open-source projects with real implementations
        - **Q&A Platforms** (problem-solving): Stack Overflow, Super User, Unix Stack Exchange for debugging
        - **Technical Blogs** (best practices, tutorials): Dev.to, Medium, Hashnode, Substack technical newsletters
        - **Tech News** (product releases, benchmarks): Hacker News, TechCrunch, Ars Technica
        **Key Indicators:** Error messages, performance metrics, latency benchmarks, API documentation quality, community adoption
        **Keywords Pattern:** "Error fix", "Documentation", "Benchmark", "Latency", "Performance issue", "Implementation guide"

        ## 4. **ACADEMIC_MEDICAL** - Research, Science, Clinical Studies, Deep Tech
        **Scope:** Biology, Medicine, Physics, Chemistry, Clinical trials, Neuroscience, AI Research, Deep Technical Research.
        **Information Sources:**
        - **Peer-Reviewed Journals** (validated research): PubMed, Google Scholar, ResearchGate publications
        - **Preprint Servers** (cutting-edge unpublished): arXiv, bioRxiv, medRxiv for latest findings
        - **Academic Institutions** (university research): .edu domains, NIH, CDC, official government health agencies
        - **Academic Papers** (detailed analysis): PDF-format dissertations, white papers, technical documentation
        **Key Indicators:** Peer review status, sample size, statistical significance, citation count, institutional affiliation
        **Keywords Pattern:** "Study", "Research findings", "Mechanism", "Clinical trial", "Data analysis", "Peer-reviewed"

        # Reference Examples (Study these carefully!)
        {FEW_SHOT_EXAMPLES}

        # Current Task
        Current Date: {{current_date}}
        User Input: "{{topic}}"

        # Output Instructions
        1. **Analyze Intent:** Don't just look at the noun (e.g., "Tesla"). Look at the verb/modifier (e.g., "Supply Chain").
        2. **Translate:** Convert the core concept to Professional English.
        3. **Select Platforms:** Choose at least 10 specific domains that host high-density information for this topic.
        4. **Output Format:** {{format_instructions}}
        """

        parser = PydanticOutputParser(pydantic_object=SearchStrategy)
        strategy_prompt = STRATEGY_SYSTEM_PROMPT.format(
            current_date=today.isoformat(),
            topic=topic,
            format_instructions=parser.get_format_instructions()
        )
        
        response = await self.llm.ainvoke(strategy_prompt)
        # 使用安全解析方法
        strategy = await safe_parse(self.llm, parser, response.content)

        # --- Phase 2: 战术执行 (Dorks Generation) ---
        domain_sources = self._get_domain_sources(strategy.domain, strategy.primary_keywords_en)
        
        # 按照来源类型组织平台列表
        platforms_by_type = []
        for source_type, sites in domain_sources.items():
            platforms_by_type.extend(sites)

        include_reddit_tagging = any(
            "reddit.com" in site for sites in domain_sources.values() for site in sites
        )

        print(f"Domain Sources for {json.dumps(platforms_by_type, indent=2)}. Include Reddit Tagging: {json.dumps(strategy.dict(), indent=2)}")

        reddit_tagging_block = ""
        if include_reddit_tagging:
            reddit_tagging_block = f"""
        Reddit Comment Tagging System (optimize intent coverage):
        1) Intent Tags (most valuable): Recommendation, Comparison, Troubleshooting, Alternatives
        2) Sentiment/Feedback (prioritize NEGATIVE - higher value):
           - Negative: Complaint, Disappointment, Regret, "not worth it", "waste of money", Rant
           - Critical: Frustration, Sarcasm, Criticism, "overrated", "overhyped"
           - Positive: Fan/Evangelist, Constructive
        3) Identity/Credibility: Expert/Verified, Power User (high Karma), Beginner
        4) Native Reddit Metadata: Sticky, Controversial, Flair (Discussion/Help/Review)
        5) Content Features: High-Engagement (deep threads), External Links

        ⚠️ CRITICAL: Negative comments are HIGH VALUE - they reveal pain points, deal-breakers, and unmet needs.
        Ensure at least 1-2 dorks target negative sentiment explicitly.

        When crafting reddit dorks, combine site:reddit.com with keywords hinting at these tags, e.g.:
        - Negative sentiment: site:reddit.com skincare (disappointed OR regret OR "not worth it" OR waste)
        - Critical/rant: site:reddit.com skincare (overrated OR overhyped OR rant OR complaint)
        - Troubleshooting: site:reddit.com troubleshooting issue error fail "need help"
        - Comparison: site:reddit.com skincare recommendation OR alternatives OR comparison
        - Controversial: site:reddit.com controversial topic flair:"discussion" after:{strategy.time_window_start}
        """
        
        dork_parser = PydanticOutputParser(pydantic_object=DorkResult)
        dork_prompt = f"""
        Role: Google Search Expert specializing in multi-source coverage.
        Context: Researching "{topic}" in domain "{strategy.domain}".
        Keywords: {strategy.primary_keywords_en}
        Time Start: {strategy.time_window_start}
        
        Task: Generate exactly 10-25 Google Dorks with DIVERSIFIED SOURCES.
        
        ⚠️ CRITICAL: Each dork MUST contain:
        1. site: directive (e.g., site:reddit.com)
        2. Actual search keywords (at least 3-5 words)
        3. Optional: time filter (after:), noise filters (-coupon)
        
        INVALID example: "site:instagram.com" (missing keywords)
        VALID example: "site:instagram.com skincare trends 2026"
        
        **CRITICAL RULE - Avoid Over-Restriction:**
        ⚠️  DO NOT limit to narrow paths like `site:reddit.com/r/SkincareAddiction`
        ✅ DO use broad platform access like `site:reddit.com`, `site:tiktok.com`, etc.
        ✅ DO cover multiple source types to get comprehensive information
        ✅ DO vary the keyword angles (pain points, trends, forecasts, controversies)
        
        **LENGTH CONSTRAINT:**
        ⚠️ IMPORTANT: Each dork MUST be ≤120 characters total (including spaces and operators).
        ✅ Keep it concise: site: + keywords + operators + time filter
        ✅ Example good length: "site:reddit.com skincare (trends OR viral) -coupon after:{strategy.time_window_start}" (varies by date)
        ❌ Example too long: Over 120 chars will be truncated by search engine!
        {reddit_tagging_block}
        
        Available Diversified Sources (pick from these):
        {json.dumps(domain_sources, indent=2)}
        
        Strategy:
        1. **Multi-Source Coverage:** Generate dorks across DIFFERENT PLATFORMS (not all from one site).
           - Example pattern: 2-3 dorks from reddit.com, 2-3 from news sites, 2 from commerce, etc.
        2. **Keyword Angles:** Vary the keywords to capture:
           - Consumer sentiment (viral, controversy, overrated, worth it)
           - Trend forecasts (upcoming, next big thing, future of, latest trends)
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
           - Good: `site:reddit.com skincare (trends OR viral OR forecast) after:{strategy.time_window_start}`
        
        Output exactly 10-25 dorks, ensuring they span different sources and perspectives.
        
        ⚠️ MANDATORY CHECKS BEFORE OUTPUT:
        1. Each dork length ≤120 characters
        2. Each dork contains BOTH site: AND keywords (not just "site:domain.com")
        3. Keywords are relevant to the research topic
        
        {dork_parser.get_format_instructions()}
        """
        
        dork_response = await self.llm.ainvoke(dork_prompt)
        # 使用安全解析方法
        result = await safe_parse(self.llm, dork_parser, dork_response.content)
        return result
