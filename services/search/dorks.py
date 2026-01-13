    
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
from services.search.domains import DOMAINS


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

    async def run(self, topic: str) -> "DorkResult":
        # 1. 获取当前时间，用于动态计算“最近一年”
        today = datetime.date.today()
        one_year_ago = today - datetime.timedelta(days=365)
        
        parser = PydanticOutputParser(pydantic_object=DorkResult)

        # --- Phase 1: 战略规划 (Strategy) ---

        # ==============================================================================
        # 1. 定义 Few-Shot Examples (这是让模型变聪明的关键)
        # ==============================================================================
        one_year_ago_str = one_year_ago.isoformat()

        FEW_SHOT_EXAMPLES = f"""
        <Example 1: The "False Friend" Trap (Animal vs. Code)>
        Input: "Python Pandas 内存溢出解决方案"
        Thinking:
        - Subject: "Pandas". Context is "memory leak" (programming).
        - Domain: Technical coding issue.
        - Audience: Developers.
        - Valid Platforms: StackOverflow (Q&A), GitHub (Code), Medium (Tutorials).
        Decision:
        - Categories: [
            "Technical Development & Code",
            "Blogs & Expert Insights"
        ]
        - Keywords_EN: ["python pandas memory leak fix", "dataframe optimization", "out of memory error"]
        - Platforms: [
            "site:stackoverflow.com",
            "site:github.com",
            "site:medium.com",
            "site:dev.to"
        ]
        </Example 1>

        <Example 2: The B2C/B2B Split (Product vs. Supply Chain)>
        Input: "Tesla Cybertruck 电池供应链分析"
        Thinking:
        - Subject: Tesla Cybertruck (B2B angle).
        - Intent: Supply Chain Analysis.
        - Source of Truth: Industry specialized news, consulting reports, and official docs.
        - Mapping:
            * Reuters -> Business News
            * SupplyChainDive -> Specialized Fields
            * McKinsey -> Consulting Reports
            * PDF/PPT -> File Types
        Decision:
        - Categories: [
            "Specialized Fields & Vertical Industries",
            "Business News & Industry Media (Soft Paywall/Free)",
            "Consulting & In-Depth Reports (Free Insights)",
            "Specific File Types & Institutional Search"
        ]
        - Keywords_EN: ["Tesla Cybertruck battery supply chain", "4680 cell suppliers", "lithium sourcing"]
        - Platforms: [
            "site:reuters.com",
            "site:supplychaindive.com",
            "site:mckinsey.com",
            "filetype:pdf",
            "filetype:pptx"
        ]
        </Example 2>

        <Example 3: The Consumer Trend (Viral/Sentiment) - MULTI-SOURCE>
        Input: "{today.year} 北美 纯净美妆 营销趋势"
        Thinking:
        - Intent: Marketing Forecast & Consumer Sentiment.
        - Source of Truth Strategy:
            1. Social Sentiment (Reddit/TikTok) -> Social Media & Community Feedback
            2. Review Data (Trustpilot) -> Reviews & Reputation Assessment
            3. Industry News (Vogue) -> Business News & Industry Media
            4. Trend Data (Google/Pinterest) -> Trend & Innovation Monitoring
            5. Market Data (ThinkWithGoogle) -> Demographics & Consumer Insights
        Decision:
        - Categories: [
            "Social Media & Community Feedback",
            "Trend & Innovation Monitoring",
            "Reviews & Reputation Assessment",
            "Business News & Industry Media (Soft Paywall/Free)",
            "Specialized Fields & Vertical Industries",
            "Demographics & Consumer Insights"
        ]
        - Keywords_EN: ["clean beauty viral trends {today.year}", "clean beauty controversy", "skincare marketing forecast"]
        - Platforms: [
            "site:reddit.com",
            "site:tiktok.com",
            "site:vogue.com",
            "site:cosmoprof.com",
            "site:thinkwithgoogle.com",
            "site:trustpilot.com",
            "site:trendhunter.com",
            "site:explodingtopics.com",
        ]
        Generated Dorks (examples):
            1. site:reddit.com clean beauty (trends OR viral OR {today.year}) -coupon -buy
            2. site:tiktok.com skincare (overrated OR controversy OR future) after:{one_year_ago_str}
            3. site:vogue.com clean beauty trends {today.year}
            4. site:thinkwithgoogle.com beauty marketing trends
            5. site:cosmoprof.com beauty industry forecast
            6. site:trustpilot.com (skincare OR cosmetics) (review OR complaint) after:{one_year_ago_str}
            7. site:trendhunter.com clean beauty marketing
            8. site:explodingtopics.com clean beauty viral products
            9. site:trends.pinterest.com skincare aesthetics {today.year}
            10. site:google.com/trends clean beauty {today.year}
        </Example 3>

        <Example 4: The Academic/Medical Niche>
        Input: "GLP-1 减肥药的长期副作用机制"
        Thinking:
        - Subject: GLP-1 (Medical).
        - Intent: Mechanism of action/side effects.
        - Source of Truth: Clinical trials, research papers.
        - Mapping:
            * NIH/PubMed/MedRxiv -> Academic & Medical Research
            * PDF -> Specific File Types
        Decision:
        - Categories: [
            "Academic & Medical Research",
            "Specific File Types & Institutional Search"
        ]
        - Keywords_EN: ["GLP-1 long term side effects mechanism", "semaglutide clinical trial results", "safety profile"]
        - Platforms: [
            "site:nih.gov",
            "site:pubmed.ncbi.nlm.nih.gov",
            "site:medrxiv.org",
            "site:scholar.google.com",
            "filetype:pdf"
        ]
        </Example 4>

        <Example 5: The Translation Trap (Consumer vs. Science)>
        Input: "乳香精油对皮肤紧致的科学依据"
        Thinking:
        - Subject: Frankincense Essential Oil.
        - Intent: Scientific Validation (Mechanism of action).
        - Domain Context:
            - Consumer Context: "Firming", "Anti-wrinkle", "Face oil".
            - Academic Context (Need Translation): "Frankincense" -> "Boswellia serrata"; "Firming" -> "Fibroblast migration", "Collagen", "Elastin", "Wound healing".
        Decision:
        - Categories: [
            "Academic & Medical Research",
            "Specific File Types & Institutional Search"
        ]
        - Keywords_EN: ["Boswellia serrata topical", "boswellic acids skin", "dermal fibrosis mechanism"]
        - Platforms: [
            "site:pubmed.ncbi.nlm.nih.gov",
            "filetype:pdf site:.edu"
        ]
        Generated Dorks:
            1. site:pubmed.ncbi.nlm.nih.gov (Boswellia OR "Frankincense oil") (fibroblast OR collagen OR elastin)
            2. filetype:pdf site:.edu "Boswellia serrata" ("dermal" OR "cutaneous") (efficacy OR clinical)
            3. filetype:pdf site:.gov "essential oils" (safety OR toxicity OR "dermatological")
        </Example 5>

        """

        # ==============================================================================
        # 2. 组装 System Prompt
        # ==============================================================================
        STRATEGY_SYSTEM_PROMPT = f"""
        # Role
        You are the **Head of Research Strategy** at an elite intelligence firm.
        Your goal is to route the user's research topic to the strictly correct **Internet Domain** and translate it into high-precision English search keywords.
        
        # Context (Strict Whitelist)
        You are functionally restricted. You may ONLY generate search queries using the domains explicitly defined in the dataset below. Use of any domain not present in this JSON is strictly prohibited.

        Allowed Sources:
        {json.dumps(DOMAINS, indent=2)}

        # Reference Examples (Study these carefully!)
        {FEW_SHOT_EXAMPLES}

        
        # Strategy:
        1.**Strict Mapping:** Check the user's topic against the 'category' and 'description' in the JSON. Even if you know a better external site, you MUST convert the intent to fit one of the Allowed Sources (e.g., use Reddit or generic News if a specific niche forum is missing).
        2. **Multi-Source Coverage:** Generate dorks across DIFFERENT PLATFORMS (not all from one site).
           - Example pattern: 2-3 dorks from reddit.com, 2-3 from news sites, 2 from commerce, etc.
        3. **Domain-Specific Vocabulary Mapping (CRITICAL):**
           - **Consumer Context (Social/News/Blogs):** Use colloquial, emotional, and marketing keywords.
             * Keywords: "skin firming", "magic serum", "wrinkle eraser", "breakout".
           - **Academic/Technical Context (.edu, .gov, PubMed):** You MUST translate consumer terms into Scientific mechanisms or Chemical names.
             * Keywords: "skin firming" → "(collagen synthesis OR fibroblast proliferation OR elasticity)"
             * Keywords: "Frankincense" → "(Boswellia serrata OR Boswellic acids)"
             * Keywords: "Anti-aging" → "(photoaging OR oxidative stress)"
        4. **Keyword Angles:** Vary the keywords to capture:
           - Consumer sentiment (viral, controversy, overrated, worth it)
           - Trend forecasts (upcoming, next big thing, future of, latest trends)
           - Pain points (issue, fail, problem, regret, side effect)
           - Insider insights (behind the scenes, strategy, marketing)
        5. **Syntax Rules:**
           - Use `site:` for platform targeting.
           - **For Academic/PDF Dorks (Optimization):** * STRICTLY REMOVE filler words (e.g., "study", "report"). 
             * USE Boolean Logic: `(ScientificTermA OR SynonymB) (MechanismA OR MechanismB)`.
           - **Filetype Constraints:** You are STRICTLY PROHIBITED from adding filetype:pdf to any domain unless the string "filetype:pdf" is explicitly part of the site field in the JSON (e.g., do not combine site:.edu with filetype:pdf dynamically). **NEVER** use it for Social Media.
           - **Date Logic:** * NEVER use hardcoded years (e.g., 2022) in the query text.
             * **Default Recency Rule:** If the user input does NOT specify a timeframe, **ALWAYS** append `after:{one_year_ago_str}` to ensure data freshness.
        6. **Anti-Noise:**
           - B2B/Tech: `-jobs -hiring -courses` (max 3 filters)
           - B2C: `-coupon -code -discount` (max 3 filters)
           - Social: Don't add noise filters; let the platform's algorithm work
        7. **Avoid Long Exact Matches:**
           - Bad: `site:reddit.com "very specific long phrase about skincare marketing trends"`
           - Good: `site:reddit.com skincare (trends OR viral OR forecast) after:{one_year_ago_str}`
        
        Generate **up to 10 dorks**. Prioritize strict adherence to the Allowed Sources over quantity. If valid sources are exhausted, stop generating.

       

        # Current Task
        Current Date: {today.isoformat()}
        User Input: "{topic}"

        # Output Instructions
        1. **Analyze Intent:** Don't just look at the noun (e.g., "Tesla"). Look at the verb/modifier (e.g., "Supply Chain").
        2. **Translate:** Convert the core concept to Professional English.
        3. **Select Platforms:** Choose 3-5 specific domains that host high-density information for this topic.
        4. **Output Format:** {parser.get_format_instructions()}
        """
        
        response = await self.llm.ainvoke(STRATEGY_SYSTEM_PROMPT)
        # 使用安全解析方法
        strategy = await safe_parse(self.llm,parser, response.content)
        return strategy
