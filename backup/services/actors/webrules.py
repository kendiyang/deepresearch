from dataclasses import dataclass, field
from typing import List

# --- 2. 站点配置 ---
@dataclass
class SiteConfig:
    domain_keyword: str
    title_selector: str
    content_selector: str 
    remove_selectors: List[str] = field(default_factory=list)
    is_medium_style: bool = False 

# === 合并后的完整规则库 ===
SITE_CONFIGS = [
    # ==================== 商业新闻与媒体 ====================
    SiteConfig(
        domain_keyword="reuters.com",
        title_selector="h1", 
        content_selector="article, .article-body__content__17Yit", 
        remove_selectors=["div[class*='masked-content']", "div[data-testid='behaviors-container']"]
    ),
    SiteConfig(
        domain_keyword="techcrunch.com",
        title_selector="h1",
        content_selector=".wp-block-post-content",
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
        remove_selectors=[".c-gallery-vertical"]
    ),

    # ==================== 咨询公司洞察 ====================
    SiteConfig(
        domain_keyword="mckinsey.com",
        title_selector="h1",
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

    # ==================== 博客平台 ====================
    SiteConfig(
        domain_keyword="medium.com",
        title_selector="h1",
        content_selector="article",
        remove_selectors=[".speechify-controls", "button"],
        is_medium_style=True
    ),
    SiteConfig(
        domain_keyword="substack.com",
        title_selector="h1.post-title, h1",
        content_selector=".body.markup, .post-body",
        remove_selectors=[".subscribe-widget", ".share-dialog"]
    ),
    SiteConfig(
        domain_keyword="dev.to",
        title_selector="h1",
        content_selector="#article-body",
        remove_selectors=[".action-space"]
    ),

    # ==================== 学术与众筹 ====================
    SiteConfig(
        domain_keyword="kickstarter.com",
        title_selector=".project-name, h2.title",
        content_selector=".story-content, #risks-and-challenges",
        remove_selectors=[".video-player", ".msg-overlay"]
    ),
    SiteConfig(
        domain_keyword="wiley.com",
        title_selector="h1.citation__title",
        content_selector=".article-section__content, #section-1-en, .article__body", 
        remove_selectors=[".references", ".accordion__footer", ".figure-viewer", ".citations"]
    ),
    SiteConfig(
        domain_keyword="mdpi.com",
        title_selector="h1",
        content_selector="article, div.art-abstract, div.html-abstract, section.abstract", 
        remove_selectors=[".art-authors", ".art-affiliations", ".bib-identity", "button", ".back-to-top"]
    ),
    SiteConfig(
        domain_keyword="trendhunter.com",
        title_selector="h1",
        content_selector="article, div.art-abstract, div.html-abstract, section.abstract, .thp__article", 
        remove_selectors=[".tha__tags",".social-sharing",".referenced-article","aside"]
    ),
    SiteConfig("arxiv.org", "h1.title", "blockquote.abstract", ["span.descriptor"]),
    SiteConfig("medrxiv.org", "#page-title", ".article.long-form", [".pane-highwire-panel-tabs"]),
    # === Forbes ===
    SiteConfig(
        domain_keyword="forbes.com",
        title_selector="h1",
        # 核心策略：直接抓取 article 标签，忽略那些随机的 class
        # 备选：也可以用 id^='article-num-' (匹配以 article-num- 开头的 ID)
        content_selector="article, [id^='article-num-']", 
        remove_selectors=[
            ".aq-embed",          # 移除广告嵌入
            ".artist-attribution", # 移除图片归属文字
            ".stream-item-text",   # 移除侧边栏/底部推荐流
            "form",
            ".share-bar"
        ]
    ),
]