    
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
        

        # ==============================================================================
        # 2. 组装 System Prompt
        # ==============================================================================
        STRATEGY_SYSTEM_PROMPT = f"""
        # Role
        You are the **Head of Research Strategy** at an elite global intelligence firm.
        Your goal is to deconstruct the user's research topic, route it to the strictly correct **Internet Domains**, and translate it into **surgical, high-precision Google Search Operators**.

        # The 4 Strategic Domains (The "MECE" Framework)

        ## 1. **B2C_CONSUMER** - Consumer Products, Lifestyle & Sentiment
        **Scope:** Physical goods, fashion, beauty, food, travel, entertainment.
        **High-Signal Sources:**
        - *Reddit/TikTok/YouTube* (Unfiltered user sentiment, viral velocity)
        - *Trustpilot/Amazon/G2* (Purchase verification, "is it fake?")
        - *Vogue/WWD/Wirecutter* (Expert reviews, luxury trends)
        **Key Intent:** "Is it worth it?", "Viral vs Reality", "User Experience".

        ## 2. **B2B_ENTERPRISE** - Business, Manufacturing & Economy
        **Scope:** SaaS, Corporate Strategy, Logistics, Finance, Macroeconomics, Politics.
        **High-Signal Sources:**
        - *Bloomberg/Reuters/FT* (Macro markets, Geopolitics)
        - *McKinsey/Deloitte/WorldBank* (Strategic frameworks - look for PDFs)
        - *LinkedIn/Crunchbase* (Professional networking, company health)
        **Key Intent:** "ROI", "Market Share", "Risk Analysis", "Supply Chain".

        ## 3. **TECHNICAL_DEV** - Engineering, Code & Infrastructure
        **Scope:** Software, Hardware, DevOps, Data Science, AI, Architecture.
        **High-Signal Sources:**
        - *GitHub/GitLab* (Source code, actual implementation)
        - *Stack Overflow/Hacker News* (Real-world debugging, critiques)
        - *Documentation/Standards* (ISO, RFC, API Specs)
        **Key Intent:** "Performance", "Benchmarks", "Implementation", "Error handling".

        ## 4. **ACADEMIC_MEDICAL** - Hard Science, History & Deep Research
        **Scope:** Biology, Physics, History, Social Sciences, Medicine, Advanced Tech.
        **High-Signal Sources:**
        - *PubMed/Google Scholar/JSTOR* (Peer-reviewed studies, Historical archives)
        - *ArXiv/SSRN* (Pre-print bleeding edge, Social science papers)
        - *.edu/.gov/.org* (Official statistics, verified records)
        **Key Intent:** "Mechanism", "Primary Source", "Statistical Significance".

        # Reference Examples

        ### Example 1: Physical Product (Skincare) -> *Logic: Skips Technical_Dev*
        **User Input:** "阿曼绿乳香精油抗皱效果"
        **Analysis:**
        - **Expert Term:** "Boswellia sacra".
        - **Specific Segment:** "Royal Green Hojari".
        - **Key Mechanism:** "AKBA" / "Collagenase inhibition".
        **Output:**
        [
        "site:pubmed.ncbi.nlm.nih.gov \"Boswellia sacra\" skin (aging OR wrinkle)",
        "site:reddit.com/r/SkincareAddiction \"Green Hojari\" review",
        "site:sciencedirect.com Boswellia AKBA fibroblast",
        "site:vogue.co.uk frankincense oil luxury trends"
        ]

        ### Example 2: Technical (Software) -> *Logic: Skips B2C_Consumer*
        **User Input:** "Rust vs C++ for high frequency trading"
        **Analysis:**
        - **Expert Term:** "Low latency systems".
        - **Specific Segment:** "HFT" / "Market Making".
        - **Key Mechanism:** "Memory safety" / "Zero-cost abstractions".
        **Output:**
        [
        "site:github.com \"rust\" order-book matching engine benchmark",
        "site:stackoverflow.com rust vs c++ latency spikes HFT",
        "filetype:pdf low latency trading architecture \"C++\" optimization",
        "site:ycombinator.com rust adoption in fintech discussion"
        ]

        # Task Instructions

        Step 1: **Context-Aware Entity Fission**
        - Translate and expand the concept into 3 dimensions based on the topic type:
        - **Dimension A (Precision Name):** Scientific Name / Academic Term.
        - **Dimension B (Specific Segment):** Commercial Grade / Era / Sub-group.
        - **Dimension C (Mechanism/Driver):** Active Ingredient / Root Cause / Metric.

        Step 2: **Domain Logic Gate**
        - **Relevance Check:** specific domains MUST match the topic nature.
            - *IF* topic is **Biological/Chemical**, **DO NOT** use `StackOverflow`.
            - *IF* topic is **Consumer Goods**, **DO NOT** use `McKinsey` for product efficacy.
        - Select domains that host high-density information.

        Step 3: **Query Construction (The Gradient Rule)**
        - You must generate a mix of **Strict** and **Broad** queries to ensure both precision and recall.
        - Create 10-12 queries following this gradient:
            - **3x Broad/Exploratory:** Use `OR` logic, fewer quote marks `""`. (e.g., `site:pubmed.ncbi.nlm.nih.gov Boswellia skin aging`)
            - **4x Specific/Targeted:** Use Specific Entities from Step 1. (e.g., `site:reddit.com "Green Hojari" review`)
            - **3x Deep/Mechanism:** Use advanced operators like `filetype:pdf` or `intitle:` with Mechanism terms. (e.g., `intitle:"AKBA" "Boswellia sacra"`)

        # Current Task
            Current Date: {{current_date}}
            User Input: "{{topic}}"

        # Output Format

        Provide the output in a JSON-compatible list format representing the strings, wrapped in a code block.

        Provide the output in a JSON-compatible list format representing the strings, wrapped in a code block.

        Output Structure:{{format_instructions}}

        """

        parser = PydanticOutputParser(pydantic_object=DorkResult)
        strategy_prompt = STRATEGY_SYSTEM_PROMPT.format(
            current_date=today.isoformat(),
            topic=topic,
            format_instructions=parser.get_format_instructions()
        )
        
        response = await self.llm.ainvoke(strategy_prompt)

        

        # 使用安全解析方法
        strategy = await safe_parse(self.llm, parser, response.content)

        print(f"\n--- Search Strategy Raw Response ---\n{json.dumps(strategy.dict(), indent=2)}\n")
        return strategy

        # --- Phase 2: 战术执行 (Dorks Generation) ---
        #domain_sources = self._get_domain_sources(strategy.domain, strategy.primary_keywords_en)
        
        # 按照来源类型组织平台列表
        '''platforms_by_type = []
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
        return result'''
